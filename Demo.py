import os
import json
from flask import Flask, request, abort
from PIL import Image
import io
import requests
import google.generativeai as genai
from google.cloud import firestore
from google.cloud import vision
from google.cloud import translate_v2 as translate
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (MessageEvent, TextMessageContent, ImageMessageContent)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)

# 設置 Google Cloud 認證
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"C:\Users\Acer EX-14\Desktop\myproject\tibame-gac242-07-0726-799f464ad6fc.json" #firestore金鑰
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"C:\Users\Acer EX-14\Desktop\myproject\tibame-gac242-07-0726-cc0865a92224.json" #cloudvision金鑰、translator金鑰

# 指定專案資料庫
project_id = "tibame-gac242-07-0726"
database_id = "tibame-firestore-07-0812"

# 初始化 Firestore 客戶端
db = firestore.Client(project=project_id, database=database_id)
print('Connection successful')

# 設定Gemini API 密鑰
api_key = "AIzaSyCeteCKvjAhBAiyJvGzsxsS_Y6AvXQHCP0"
genai.configure(api_key=api_key)

# 連接GEMINI模型
model = genai.GenerativeModel('gemini-1.5-flash')

# 設定 LINE BOT 的 Channel Secret 和 Channel Access Token
configuration = Configuration(
    access_token="ER82qXDoYiDv++8LdQRofqDVHtLkT6hIeYZlWgQINiOvKM4IGB8r1148E034y8LMOL6vSNjmf11Xpo0yFHNgBQrX2cXRzy4zJ3Elx7cUa7Wd3DnV2L/+zomk8oxwkgcNgI0A+cUMrBPldNo8ChK6ugdB04t89/1O/w1cDnyilFU="
)
handler = WebhookHandler('aa06b5591296b3ca027ce6d84bee1011') # Channel Secret

app = Flask(__name__)

# LINE BOT的 Webhook 路由
@app.route("/callback", methods=['POST'])
def callback():
    # 獲取 X-Line-Signature 標頭值
    signature = request.headers['X-Line-Signature']
    # 將請求主體轉換為文本
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # 解析 webhook 主體
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 處理文字訊息
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text
    
    # 使用Gemini分析使用者意圖
    prompt_event = f"使用者:{text}\n請分析使用者想對物品做什麼，回覆格式限定為這三種行為之一:存放物品、查詢物品、修改物品。不做多餘的回答"
    response_event = model.generate_content(prompt_event) #Gemini分析行為
    prompt_info = f"""
                    使用者：{text}
                    請分析使用者這段話的物品關鍵資訊，數量以阿拉伯數字顯示，並直接json輸出不標示檔案類型，然後不顯示出json與附屬標點符號，僅顯示字典括號內的內容並包括括號。
                    {{
                        "name": "物品名稱",
                        "quantity": "數量",
                        "category": "種類",
                        "location": "存放位置"
                    }}
                    如果使用者未提及存放位置，請填入 None，未提及類別，則自行針對物品來判斷賦予一個類別。
                    """
    response_info = model.generate_content(prompt_info) #透過gemini提取關鍵資訊並輸出json
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

    try:
         # 解析 Gemini 的回應，提取指令
        if "存放物品" in response_event.text:
            try:
                item_data = json.loads(response_info.text)
                # 寫進Firestore的同時新增時間至 JSON 資料
                item_data['start_time'] = firestore.SERVER_TIMESTAMP
                # 將資料寫入 Firestore
                doc_ref = db.collection('items').document()
                doc_ref.set(item_data)
                print(item_data)

            except json.JSONDecodeError:
                print("JSON 解析錯誤")
                print(response_info.text)

            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="好的，已存放完成")])
            )

        elif "查詢物品" in response_event.text:
            try:
                item_data = json.loads(response_info.text)
                item_name = item_data.get("name", None)
                
                if item_name:
                    # 預處理階段：清除數量為0的文檔
                    docs_to_check = db.collection('items').where("name", "==", item_name).stream()

                    for doc in docs_to_check:
                        item_info = doc.to_dict()
                        if int(item_info.get('quantity', 0)) == 0:
                            doc.reference.delete()

                    # 在 Firestore 中查找所有匹配該物品名稱且位置不為 None 的文檔
                    docs = db.collection('items').where("name", "==", item_name).stream()

                    # 初始化總數量
                    total_quantity = 0
                    items_info = []

                    for doc in docs:
                        item_info = doc.to_dict()
                        # 過濾掉 location 為 None 的物品
                        if item_info.get('location') != "None":
                            try:
                                # 確保 quantity 是整數類型
                                quantity = int(item_info.get('quantity', 0))
                                total_quantity += quantity
                                items_info.append(item_info)
                            except ValueError:
                                print(f"無法解析數量：{item_info.get('quantity')}")

                    if items_info:
                        # 構造給 Gemini 的最終 Prompt，包含所有匹配文檔的資訊和總數量
                        all_items_details = "\n".join([
                            f"名稱：{item['name']}，數量：{item['quantity']}，存放位置：{item['location']}"
                            for item in items_info
                        ])
                        prompt_final = f"""
                            查詢了 {item_name}。
                            總共找到 {total_quantity} 個 {item_name}。
                            以下是該物品的所有資訊：
                            {all_items_details}
                            請生成一個友好的回答來告知使用者這些資訊
                            告知資訊即可，不做額外的詢問。
                        """
                        response_final = model.generate_content(prompt_final)

                        # 回覆使用者最終的訊息
                        line_bot_api.reply_message_with_http_info(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text=response_final.text)])
                        )
                    else:
                        # 未找到物品的處理
                        line_bot_api.reply_message_with_http_info(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text="未找到該物品，請確認名稱是否正確。")])
                        )
                else:
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="無法從您的訊息中提取物品名稱，請重新嘗試。")])
                    )

            except json.JSONDecodeError:
                print("JSON 解析錯誤")
                print(response_info.text)


        elif "修改物品" in response_event.text:
            try:
                # 使用Gemini提取使用者要修改的物品資訊
                item_data = json.loads(response_info.text)
                item_name = item_data.get("name", None)
                change_quantity = item_data.get("quantity", None)  # 修改或移動的數量
                new_location = item_data.get("location", None)  # 新的位置
                
                if item_name:
                    # 在 Firestore 中查找匹配該物品名稱的文檔
                    docs = db.collection('items').where("name", "==", item_name).stream()

                    doc_found = False  # 標記是否找到匹配的文檔

                    for doc in docs:
                        doc_found = True
                        item_info = doc.to_dict()
                        current_quantity = int(item_info.get('quantity', 0))

                        if change_quantity is not None:
                            try:
                                # 解析並處理數量變化
                                change_quantity = int(change_quantity)
                                
                                # 情況1：增加數量到當前位置
                                if change_quantity > 0 and new_location is None:
                                    updated_quantity = current_quantity + change_quantity
                                    item_info['quantity'] = updated_quantity
                                    
                                    # 更新原始文檔
                                    doc.reference.set(item_info)

                                    reply_message = f"物品 {item_name} 的數量已增加。\n" \
                                                    f"新數量：{item_info['quantity']}。"
                                
                                # 情況2：移動部分數量到新位置
                                elif change_quantity > 0 and new_location is not None:
                                    if change_quantity <= current_quantity:
                                        updated_quantity = current_quantity - change_quantity
                                        item_info['quantity'] = updated_quantity
                                        
                                        if updated_quantity == 0:
                                            # 如果原始位置的數量減少到0，則刪除該文檔
                                            doc.reference.delete()
                                        else:
                                            # 更新原始位置的數量
                                            doc.reference.set(item_info)

                                        # 創建新文檔，表示移動後的物品
                                        new_item_info = item_info.copy()
                                        new_item_info['quantity'] = change_quantity
                                        new_item_info['location'] = new_location
                                        db.collection('items').document().set(new_item_info)

                                        reply_message = f"已將 {change_quantity} 個 {item_name} 移至 {new_location}。\n" \
                                                        f"原位置剩餘數量：{item_info.get('quantity', 0)} 個。"
                                    else:
                                        reply_message = "移動的數量超過了現有的數量，請重新確認。"

                                # 情況3：減少數量（新位置為None時）
                                elif change_quantity < 0 and new_location is None:
                                    updated_quantity = current_quantity + change_quantity
                                    if updated_quantity <= 0:
                                        # 如果數量減少到0或以下，刪除文檔
                                        doc.reference.delete()
                                        reply_message = f"物品 {item_name} 已完全移除。"
                                    else:
                                        item_info['quantity'] = updated_quantity
                                        # 更新原始文檔
                                        doc.reference.set(item_info)
                                        reply_message = f"物品 {item_name} 的數量已減少。\n" \
                                                        f"新數量：{item_info['quantity']}。"

                                else:
                                    reply_message = "未指定有效的數量或位置，請重新確認。"
                                
                                # 回覆使用者
                                line_bot_api.reply_message_with_http_info(
                                    ReplyMessageRequest(
                                        reply_token=event.reply_token,
                                        messages=[TextMessage(text=reply_message)])
                                )
                                break
                            
                            except ValueError:
                                print(f"無法解析數量：{item_info.get('quantity')} 或 {change_quantity}")
                        else:
                            line_bot_api.reply_message_with_http_info(
                                ReplyMessageRequest(
                                    reply_token=event.reply_token,
                                    messages=[TextMessage(text="未指定要移動或修改的數量。")])
                            )
                    
                    if not doc_found:
                        line_bot_api.reply_message_with_http_info(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text="未找到該物品，請確認名稱是否正確。")])
                        )
                
                else:
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="無法從您的訊息中提取物品名稱，請重新嘗試。")])
                    )

            except json.JSONDecodeError:
                print("JSON 解析錯誤")
                print(response_info.text)

        
    except Exception as e:
        # 捕捉所有異常
        line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"發生錯誤：{str(e)}，請稍後再試。")])
            )
        print(f"Error: {str(e)}")

# 處理圖片訊息
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

    # 獲取圖片內容
    message_id = event.message.id
    content_url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    headers = {"Authorization": f"Bearer {configuration.access_token}"}
    
    # 下載圖片內容
    img_response = requests.get(content_url, headers=headers)

    if img_response.status_code == 200:
        # 將圖像內容加載為PIL對象
        img = Image.open(io.BytesIO(img_response.content))
        # 調用圖像識別功能
        recognized_items = recognize_items_from_image(img)
        
        # 調用存儲函數，將識別出的物品資訊存入資料庫
        store_items_in_db(recognized_items)
        
        # 回覆使用者識別結果
        reply_text = "已識別並存入的物品:\n" + "\n".join([item["name"] for item in recognized_items])
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )
    else:
        # 處理獲取圖片失敗的情況
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="無法獲取圖片內容，請稍後再試。")]
            )
        )

def translate_text(text, target_language="zh-TW"):
    translate_client = translate.Client()
    result = translate_client.translate(text, target_language=target_language)
    return result["translatedText"]

def recognize_items_from_image(img):
    client = vision.ImageAnnotatorClient()
    
    # 將PIL圖像轉換為字節
    content = io.BytesIO()
    img.save(content, format="JPEG")
    image = vision.Image(content=content.getvalue())

    # 調用Google Cloud Vision API
    response = client.label_detection(image=image)
        
    labels = response.label_annotations

    recognized_items = []
    for label in labels:
        translated_name = translate_text(label.description)  # 將英文類別翻譯成中文
        recognized_items.append({
            "name": translated_name,  # 存儲翻譯後的中文名稱
            "category": "自動分類"  # 可以添加更複雜的分類邏輯
        })
        
    return recognized_items

# 將識別出的物品存入資料庫
def store_items_in_db(recognized_items):
    for item in recognized_items:
        item_data = {
            "name": item["name"],
            "quantity": 1,  # 初始默認為1
            "location": "未指定",  # 初始位置未指定
            "category": "自動分類",
            "start_time": firestore.SERVER_TIMESTAMP
        }
        db.collection('items').document().set(item_data)

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5003))
    app.run(host='0.0.0.0', port=port, debug=True)

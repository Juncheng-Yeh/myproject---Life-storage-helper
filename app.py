import os
import json
from flask import Flask, request, abort
from PIL import Image
import io
import google.generativeai as genai
from google.cloud import firestore
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (MessageEvent, TextMessageContent)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    AudioMessage
)

# 設置 Google Cloud 認證
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = r"C:\Users\Acer EX-14\Desktop\myproject\tibame-gac242-07-0726-799f464ad6fc.json"

#指定專案資料庫
project_id = "tibame-gac242-07-0726"
database_id = "tibame-firestore-07-0812"

# 初始化 Firestore 客戶端
db = firestore.Client(project=project_id, database=database_id)
print('Connection successful')

# 設定Gemini API 密鑰
api_key = "AIzaSyCeteCKvjAhBAiyJvGzsxsS_Y6AvXQHCP0"
genai.configure(api_key=api_key)

#連接GEMINI模型
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
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']
    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # parse webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 處理文字訊息
@handler.add(MessageEvent, message=TextMessageContent)
def message_text(event):
    text = event.message.text
    
    # 使用Gemini分析使用者意圖"
    prompt_event = f"使用者:{text}\n請分析使用者想對物品做什麼，回覆格式限定為這兩種行為之一:存放物品、查詢物品。不做多餘的回答"
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
                    如果使用者未提及某項資訊，請填入 Null。
                    """
    response_info = model.generate_content(prompt_info)
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
            #EX:text:我想知道有沒有可樂->prompt_event->response_event生成"查詢物品"->"可樂"被讀取到->查詢資料庫裏面的可樂數量

            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=response_info.text)])
            )
        elif "修改物品" in response_event.text:


            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=response_info.text)])
            )

        
    except Exception as e:
        # 捕捉所有異常
        line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"發生錯誤：{str(e)}，請稍後再試。")])
            )
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5003))
    app.run(host='0.0.0.0', port=port, debug=True)
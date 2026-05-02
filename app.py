from flask import Flask, request, jsonify
import requests
import json

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_INVOICE_FIELD = "UF_CRM_1777722871188"
DOC_DEAL_LINK_FIELD = "UF_CAT_STORE_DOCUMENT_A_1777549444"

# Храним последние данные
last_data = {"raw": "нет данных"}

@app.route('/', methods=['POST'])
def handler():
    global last_data
    data = request.get_json(silent=True) or {}
    last_data = data
    return jsonify({"result": True}), 200

@app.route('/debug', methods=['GET'])
def debug():
    return jsonify(last_data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

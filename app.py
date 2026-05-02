from flask import Flask, request, jsonify
import requests
from urllib.parse import unquote

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_INVOICE_FIELD = "UF_CRM_1777722871188"
DOC_DEAL_LINK_FIELD = "UF_CAT_STORE_DOCUMENT_A_1777549444"

@app.route('/', methods=['POST'])
def handler():
    # Битрикс шлёт данные в form-urlencoded, а не JSON
    data = request.form.to_dict()
    
    # Раскодируем вложенные данные
    fields = {}
    for key, value in data.items():
        if key.startswith('data[FIELDS]['):
            # Вытаскиваем имя поля, например ID из data[FIELDS][ID]
            field_name = key.split('[')[-1].replace(']', '')
            fields[field_name] = value
    
    doc_number = fields.get('NUMBER') or fields.get('TITLE') or fields.get('ID', '')
    deal_id = fields.get(DOC_DEAL_LINK_FIELD)
    
    if not deal_id:
        return jsonify({"result": True, "msg": "deal not linked", "fields": list(fields.keys())}), 200
    
    # Обновляем сделку
    payload = {"id": int(deal_id), "fields": {DEAL_INVOICE_FIELD: doc_number}}
    resp = requests.post(f"{WEBHOOK_URL}crm.deal.update.json", json=payload, timeout=10)
    
    return jsonify({"result": True, "deal": deal_id, "number": doc_number, "bitrix_status": resp.status_code}), 200

@app.route('/debug', methods=['GET'])
def debug():
    return "OK"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

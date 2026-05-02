from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_INVOICE_FIELD = "UF_CRM_1777722871188"
DEAL_ID_FIELD = "UF_CATALOG_1777747222711"

last_debug = {}

@app.route('/', methods=['POST'])
def handler():
    global last_debug
    
    data = request.form.to_dict()
    
    fields = {}
    for key, value in data.items():
        if key.startswith('data[FIELDS]['):
            field_name = key.split('[')[-1].replace(']', '')
            fields[field_name] = value
    
    last_debug['fields'] = fields
    
    doc_entity_id = fields.get('ENTITY_ID', '')  # Это ID накладной
    deal_id = fields.get(DEAL_ID_FIELD, '')       # Это ID сделки из кастомного поля
    
    last_debug['doc_entity_id'] = doc_entity_id
    last_debug['deal_id'] = deal_id
    
    if not deal_id:
        return jsonify({"result": True, "msg": "deal_id not found"}), 200
    
    # Вписываем ID накладной в поле сделки
    payload = {"id": int(deal_id), "fields": {DEAL_INVOICE_FIELD: doc_entity_id}}
    resp = requests.post(f"{WEBHOOK_URL}crm.deal.update.json", json=payload, timeout=10)
    
    last_debug['update_status'] = resp.status_code
    
    return jsonify({"result": True, "deal": deal_id, "doc_entity_id": doc_entity_id}), 200

@app.route('/debug', methods=['GET'])
def debug():
    return jsonify(last_debug)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_INVOICE_FIELD = "UF_CRM_1777722871188"

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
    
    doc_id = fields.get('ID', '')
    deal_id = fields.get('ENTITY_ID', '')
    
    last_debug['doc_id'] = doc_id
    last_debug['deal_id'] = deal_id
    
    if not deal_id:
        return jsonify({"result": True, "msg": "no entity_id"}), 200
    
    # Получаем данные накладной по ID
    doc_resp = requests.post(
        f"{WEBHOOK_URL}entity.item.get.json",
        json={"ENTITY": "documentgenerator", "ID": int(doc_id)},
        timeout=10
    ).json()
    
    last_debug['doc_response'] = doc_resp
    
    doc_number = ''
    if doc_resp.get('result'):
        doc_number = doc_resp['result'].get('NUMBER', '')
    
    if not doc_number:
        # Пробуем получить через crm.item.get
        doc_resp2 = requests.post(
            f"{WEBHOOK_URL}crm.item.get.json",
            json={"entityTypeId": fields.get('ENTITY_TYPE_ID', 33), "id": int(doc_id)},
            timeout=10
        ).json()
        last_debug['doc_response2'] = doc_resp2
        if doc_resp2.get('result', {}).get('item'):
            doc_number = doc_resp2['result']['item'].get('title', '') or str(doc_id)
    
    if not doc_number:
        doc_number = str(doc_id)
    
    last_debug['doc_number'] = doc_number
    
    # Обновляем сделку
    payload = {"id": int(deal_id), "fields": {DEAL_INVOICE_FIELD: doc_number}}
    resp = requests.post(f"{WEBHOOK_URL}crm.deal.update.json", json=payload, timeout=10)
    last_debug['update_response'] = resp.json() if resp.ok else resp.text
    
    return jsonify({"result": True}), 200

@app.route('/debug', methods=['GET'])
def debug():
    return jsonify(last_debug)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

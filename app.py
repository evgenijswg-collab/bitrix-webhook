from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_LINK_FIELD = "UF_CRM_1777825383755"
BITRIX_DOMAIN = "mariomicci.bitrix24.ru"

last_debug = {}

@app.route('/', methods=['POST'])
def handler():
    global last_debug
    last_debug = {}
    
    data = request.form.to_dict()
    last_debug['raw_keys'] = list(data.keys())
    
    fields = {}
    for key, value in data.items():
        if key.startswith('data[FIELDS]['):
            field_name = key.split('[')[-1].replace(']', '')
            fields[field_name] = value
    
    last_debug['fields'] = fields
    
    doc_entity_id = fields.get('ENTITY_ID', '')
    last_debug['doc_entity_id'] = doc_entity_id
    
    if not doc_entity_id:
        return jsonify({"result": True, "msg": "no entity_id"}), 200
    
    # Получаем docNumber через API
    resp = requests.post(
        f"{WEBHOOK_URL}catalog.document.list.json",
        json={"filter": {"ID": int(doc_entity_id)} },
        timeout=10
    ).json()
    
    last_debug['api_response'] = resp
    
    documents = resp.get('result', {}).get('documents', [])
    last_debug['documents_found'] = len(documents)
    
    if not documents:
        return jsonify({"result": True, "msg": "document not found"}), 200
    
    doc_number = documents[0].get('docNumber', '')
    last_debug['docNumber'] = doc_number
    deal_id = doc_number
    
    if not deal_id:
        return jsonify({"result": True, "msg": "no deal_id"}), 200
    
    doc_link = f"https://{BITRIX_DOMAIN}/shop/documents/details/{doc_entity_id}/"
    last_debug['doc_link'] = doc_link
    last_debug['deal_id'] = deal_id
    
    payload = {"id": int(deal_id), "fields": {DEAL_LINK_FIELD: doc_link}}
    update_resp = requests.post(f"{WEBHOOK_URL}crm.deal.update.json", json=payload, timeout=10)
    last_debug['update_status'] = update_resp.status_code
    last_debug['update_response'] = update_resp.json() if update_resp.ok else update_resp.text
    
    return jsonify({"result": True}), 200

@app.route('/debug', methods=['GET'])
def debug():
    return jsonify(last_debug)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

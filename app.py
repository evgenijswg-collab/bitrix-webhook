from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
BITRIX_DOMAIN = "mariomicci.bitrix24.ru"

DEAL_LINK_FIELD = "UF_CRM_1777825383755"
SMART_LINK_FIELD = "UF_CRM_10_1777827987160"
SMART_ENTITY_TYPE_ID = 1046

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
    
    doc_entity_id = fields.get('ENTITY_ID', '')
    last_debug['doc_entity_id'] = doc_entity_id
    
    if not doc_entity_id:
        return jsonify({"result": True, "msg": "no entity_id"}), 200
    
    resp = requests.post(
        f"{WEBHOOK_URL}catalog.document.list.json",
        json={"filter": {"id": int(doc_entity_id)}, "select": ["id", "docNumber", "docType"]},
        timeout=10
    ).json()
    last_debug['catalog_response'] = resp
    
    documents = resp.get('result', {}).get('documents', [])
    if not documents:
        return jsonify({"result": True, "msg": "not found"}), 200
    
    doc = documents[0]
    parent_deal_id = doc.get('docNumber', '')
    doc_type = doc.get('docType', '')
    last_debug['docNumber'] = parent_deal_id
    last_debug['docType'] = doc_type
    
    doc_link = f"https://{BITRIX_DOMAIN}/shop/documents/details/{doc_entity_id}/"
    last_debug['doc_link'] = doc_link
    
    if doc_type == "A":
        update_url = f"{WEBHOOK_URL}crm.deal.update.json"
        payload = {"id": int(parent_deal_id), "fields": {DEAL_LINK_FIELD: doc_link}}
        last_debug['target_type'] = "deal"
    else:
        # Ищем по XML_ID
        search_resp = requests.post(
            f"{WEBHOOK_URL}crm.item.list.json",
            json={
                "entityTypeId": SMART_ENTITY_TYPE_ID,
                "filter": {"xmlId": parent_deal_id},
                "select": ["id", "xmlId"]
            },
            timeout=10
        ).json()
        last_debug['search_response'] = search_resp
        
        items = search_resp.get('result', {}).get('items', [])
        last_debug['items_found'] = len(items)
        
        if not items:
            return jsonify({"result": True, "msg": "no smart item found"}), 200
        
        smart_item_id = items[0]['id']
        last_debug['smart_item_id'] = smart_item_id
        
        update_url = f"{WEBHOOK_URL}crm.item.update.json"
        payload = {
            "entityTypeId": SMART_ENTITY_TYPE_ID,
            "id": smart_item_id,
            "fields": {SMART_LINK_FIELD: doc_link}
        }
        last_debug['target_type'] = "smart"
    
    update_resp = requests.post(update_url, json=payload, timeout=10)
    last_debug['update_status'] = update_resp.status_code
    last_debug['update_response'] = update_resp.json() if update_resp.ok else update_resp.text
    
    return jsonify({"result": True}), 200

@app.route('/debug', methods=['GET'])
def debug():
    return jsonify(last_debug)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

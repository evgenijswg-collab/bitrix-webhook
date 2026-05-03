from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_LINK_FIELD = "UF_CRM_1777825383755"
BITRIX_DOMAIN = "mariomicci.bitrix24.ru"

@app.route('/', methods=['POST'])
def handler():
    data = request.form.to_dict()
    
    fields = {}
    for key, value in data.items():
        if key.startswith('data[FIELDS]['):
            field_name = key.split('[')[-1].replace(']', '')
            fields[field_name] = value
    
    doc_entity_id = fields.get('ENTITY_ID', '')
    
    if not doc_entity_id:
        return jsonify({"result": True, "msg": "no entity_id"}), 200
    
    # Получаем docNumber через API
    resp = requests.post(
        f"{WEBHOOK_URL}catalog.document.list.json",
        json={"filter": {"ID": int(doc_entity_id)}, "select": ["ID", "docNumber"]},
        timeout=10
    ).json()
    
    documents = resp.get('result', {}).get('documents', [])
    if not documents:
        return jsonify({"result": True, "msg": "document not found"}), 200
    
    doc_number = documents[0].get('docNumber', '')
    deal_id = doc_number
    
    if not deal_id:
        return jsonify({"result": True, "msg": "no deal_id in docNumber"}), 200
    
    # Формируем ссылку
    doc_link = f"https://{BITRIX_DOMAIN}/shop/documents/details/{doc_entity_id}/"
    
    # Обновляем сделку
    payload = {"id": int(deal_id), "fields": {DEAL_LINK_FIELD: doc_link}}
    update_resp = requests.post(f"{WEBHOOK_URL}crm.deal.update.json", json=payload, timeout=10)
    
    return jsonify({
        "result": True,
        "deal": deal_id,
        "doc_link": doc_link,
        "update_status": update_resp.status_code
    }), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

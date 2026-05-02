from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_INVOICE_FIELD = "UF_CRM_1777722871188"
DOC_DEAL_LINK_FIELD = "UF_CAT_STORE_DOCUMENT_A_1777549444"

@app.route('/', methods=['POST'])
def handler():
    data = request.get_json(silent=True) or {}
    doc = data.get('data', {}).get('FIELDS', {})
    
    doc_number = doc.get('NUMBER') or doc.get('TITLE') or str(doc.get('ID', ''))
    deal_id = doc.get(DOC_DEAL_LINK_FIELD)
    
    if not deal_id:
        return jsonify({"result": True, "msg": "deal not linked"}), 200
    
    # Комментарий для отладки
    comment = f"Webhook debug:\nНомер: {doc_number}\nID сделки: {deal_id}"
    requests.post(
        f"{WEBHOOK_URL}crm.timeline.comment.add.json",
        json={"fields": {"ENTITY_ID": int(deal_id), "ENTITY_TYPE": "deal", "COMMENT": comment}},
        timeout=10,
    )
    
    # Обновляем сделку
    payload = {"id": int(deal_id), "fields": {DEAL_INVOICE_FIELD: doc_number}}
    resp = requests.post(f"{WEBHOOK_URL}crm.deal.update.json", json=payload, timeout=10)
    
    return jsonify({"result": True, "deal": deal_id, "number": doc_number}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

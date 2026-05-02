from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_INVOICE_FIELD = "UF_CRM_1777722871188"

@app.route('/', methods=['POST'])
def handler():
    data = request.form.to_dict()
    
    fields = {}
    for key, value in data.items():
        if key.startswith('data[FIELDS]['):
            field_name = key.split('[')[-1].replace(']', '')
            fields[field_name] = value
    
    doc_id = fields.get('ID', '')
    deal_id = fields.get('ENTITY_ID', '')  # В документе генератора ENTITY_ID — это ID сделки!
    
    if deal_id and doc_id:
        # Обновляем поле в сделке номером документа
        payload = {"id": int(deal_id), "fields": {DEAL_INVOICE_FIELD: doc_id}}
        resp = requests.post(f"{WEBHOOK_URL}crm.deal.update.json", json=payload, timeout=10)
        return jsonify({
            "result": True,
            "deal_id": deal_id,
            "doc_id": doc_id,
            "update_status": resp.status_code,
            "update_response": resp.json() if resp.ok else resp.text
        }), 200
    
    return jsonify({"result": False, "fields": fields}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

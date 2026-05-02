from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_INVOICE_FIELD = "UF_CRM_1777722871188"
DOC_DEAL_LINK_FIELD = "UF_CAT_STORE_DOCUMENT_A_1777549444"

last_debug = {}

@app.route('/', methods=['POST'])
def handler():
    global last_debug
    
    data = request.form.to_dict()
    last_debug['raw_keys'] = list(data.keys())
    
    fields = {}
    for key, value in data.items():
        if key.startswith('data[FIELDS]['):
            field_name = key.split('[')[-1].replace(']', '')
            fields[field_name] = value
    
    last_debug['fields'] = fields
    last_debug['fields_keys'] = list(fields.keys())
    
    doc_number = fields.get('NUMBER') or fields.get('TITLE') or fields.get('ID', '')
    deal_id = fields.get(DOC_DEAL_LINK_FIELD)
    
    last_debug['doc_number'] = doc_number
    last_debug['deal_id'] = deal_id
    
    if deal_id:
        payload = {"id": int(deal_id), "fields": {DEAL_INVOICE_FIELD: doc_number}}
        resp = requests.post(f"{WEBHOOK_URL}crm.deal.update.json", json=payload, timeout=10)
        last_debug['bitrix_response'] = resp.json() if resp.ok else resp.text
    
    return jsonify({"result": True}), 200

@app.route('/debug', methods=['GET'])
def debug():
    return jsonify(last_debug)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

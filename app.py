from flask import Flask, request, jsonify
import requests
import json
import sys

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_INVOICE_FIELD = "UF_CAT_STORE_DOCUMENT_A_1777549444"
DOC_DEAL_LINK_FIELD = "UF_CRM_1777722871188"

@app.route('/', methods=['POST'])
def bitrix_handler():
    try:
        data = request.get_json(force=True, silent=True)
        print("===== GOT REQUEST =====")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        
        if not data:
            print("ERROR: No JSON data received")
            return jsonify({"status": "no data"}), 200
            
        doc_number = data.get('data', {}).get('NUMBER')
        deal_id = data.get('data', {}).get(DOC_DEAL_LINK_FIELD)
        
        print(f"doc_number: {doc_number}")
        print(f"deal_id: {deal_id}")
        
        if not (doc_number and deal_id):
            print("SKIP: Missing doc_number or deal_id")
            return jsonify({"status": "skipped"}), 200
            
        payload = {"id": deal_id, "fields": {DEAL_INVOICE_FIELD: doc_number}}
        print(f"Sending to Bitrix: {json.dumps(payload)}")
        
        resp = requests.post(f"{WEBHOOK_URL}crm.deal.update.json", json=payload, timeout=10)
        print(f"Bitrix response: {resp.status_code} {resp.text}")
        resp.raise_for_status()
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

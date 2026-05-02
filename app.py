from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_INVOICE_FIELD = "UF_CAT_STORE_DOCUMENT_A_1777549444"
DOC_DEAL_LINK_FIELD = "UF_CRM_1777722871188"

@app.route('/', methods=['POST'])
def bitrix_handler():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"status": "no data"}), 200
        doc_number = data.get('data', {}).get('NUMBER')
        deal_id = data.get('data', {}).get(DOC_DEAL_LINK_FIELD)
        if not (doc_number and deal_id):
            return jsonify({"status": "skipped"}), 200
        payload = {"id": deal_id, "fields": {DEAL_INVOICE_FIELD: doc_number}}
        resp = requests.post(f"{WEBHOOK_URL}crm.deal.update.json", json=payload, timeout=10)
        resp.raise_for_status()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

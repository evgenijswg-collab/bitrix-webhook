from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_INVOICE_FIELD = "UF_CRM_1777722871188"
DOC_DEAL_LINK_FIELD = "UF_CAT_STORE_DOCUMENT_A_1777549444"

@app.route('/', methods=['POST'])
def bitrix_handler():
    try:
        data = request.get_json(force=True, silent=True)
        
        # Вытаскиваем ID документа и данные
        doc_data = data.get('data', {})
        
        # Пробуем разные варианты полей
        doc_number = doc_data.get('NUMBER') or doc_data.get('TITLE') or doc_data.get('ID')
        deal_id = doc_data.get(DOC_DEAL_LINK_FIELD)
        
        # Если UF поле пустое — пробуем найти в FIELDS
        if not deal_id and 'FIELDS' in doc_data:
            deal_id = doc_data['FIELDS'].get(DOC_DEAL_LINK_FIELD)
        
        # Если всё ещё нет — пробуем найти любое поле с "DEAL" или "UF_CRM"
        if not deal_id:
            for key in doc_data.keys():
                if 'DEAL' in key.upper() or 'UF_CRM' in key:
                    deal_id = doc_data.get(key)
                    if deal_id:
                        break
        
        if not (doc_number and deal_id):
            return jsonify({
                "status": "debug",
                "doc_number_found": doc_number,
                "deal_id_found": deal_id,
                "all_fields": list(doc_data.keys())
            }), 200
            
        payload = {"id": deal_id, "fields": {DEAL_INVOICE_FIELD: doc_number}}
        resp = requests.post(f"{WEBHOOK_URL}crm.deal.update.json", json=payload, timeout=10)
        
        return jsonify({"status": "ok", "deal_updated": deal_id}), 200
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

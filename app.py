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
        doc_data = data.get('data', {})
        
        # Пробуем найти номер
        doc_number = doc_data.get('NUMBER') or doc_data.get('TITLE') or str(doc_data.get('ID', ''))
        
        # Пробуем найти ID сделки
        deal_id = doc_data.get(DOC_DEAL_LINK_FIELD)
        if not deal_id and 'FIELDS' in doc_data:
            deal_id = doc_data['FIELDS'].get(DOC_DEAL_LINK_FIELD)
        if not deal_id:
            for key, value in doc_data.items():
                if 'DEAL' in key.upper() or 'UF_CRM' in key:
                    deal_id = value
                    break
        
        # Всегда пишем комментарий для отладки
        comment_text = f"Webhook debug:\nНомер: {doc_number}\nID сделки: {deal_id}\nПоля: {list(doc_data.keys())}"
        
        if deal_id:
            requests.post(f"{WEBHOOK_URL}crm.timeline.comment.add.json", json={
                "fields": {
                    "ENTITY_ID": deal_id,
                    "ENTITY_TYPE": "deal",
                    "COMMENT": comment_text
                }
            }, timeout=10)
        
        if not (doc_number and deal_id):
            return jsonify({"status": "debug", "comment_sent": bool(deal_id)}), 200
        
        # Обновляем сделку
        payload = {"id": deal_id, "fields": {DEAL_INVOICE_FIELD: doc_number}}
        resp = requests.post(f"{WEBHOOK_URL}crm.deal.update.json", json=payload, timeout=10)
        
        return jsonify({"status": "ok", "deal": deal_id, "number": doc_number}), 200
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

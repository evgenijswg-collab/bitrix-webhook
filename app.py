from flask import Flask, request, jsonify
import requests
import json

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
DEAL_INVOICE_FIELD = "UF_CRM_1777722871188"
DOC_DEAL_LINK_FIELD = "UF_CAT_STORE_DOCUMENT_A_1777549444"

@app.route('/', methods=['POST'])
def handler():
    data = request.get_json(silent=True) or {}
    
    # Пробуем добавить тестовый комментарий к сделке с ID 1 (или любой существующей)
    test_comment = f"DEBUG WEBHOOK:\n{json.dumps(data, indent=2, ensure_ascii=False)[:500]}"
    
    try:
        # Пробуем в сделку с ID из запроса
        deal_id = data.get('data', {}).get('FIELDS', {}).get(DOC_DEAL_LINK_FIELD)
        if not deal_id:
            deal_id = data.get('data', {}).get(DOC_DEAL_LINK_FIELD)
        if not deal_id:
            # Ищем любое поле с ID сделки
            fields = data.get('data', {}).get('FIELDS', {}) or data.get('data', {})
            for key, value in fields.items():
                if 'DEAL' in key.upper() or key == DOC_DEAL_LINK_FIELD:
                    deal_id = value
                    break
        
        if deal_id:
            comment_resp = requests.post(
                f"{WEBHOOK_URL}crm.timeline.comment.add.json",
                json={"fields": {"ENTITY_ID": int(deal_id), "ENTITY_TYPE": "deal", "COMMENT": test_comment}},
                timeout=10
            )
            return jsonify({
                "result": True, 
                "deal_id": deal_id,
                "comment_status": comment_resp.status_code,
                "data_keys": list(data.keys()),
                "fields_keys": list(data.get('data', {}).get('FIELDS', data.get('data', {})).keys())
            }), 200
        else:
            return jsonify({
                "result": False,
                "msg": "no deal_id found",
                "data_keys": list(data.keys()),
                "raw_data": str(data)[:300]
            }), 200
            
    except Exception as e:
        return jsonify({"error": str(e)}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

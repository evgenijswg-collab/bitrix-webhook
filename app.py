from flask import Flask, request, jsonify

app = Flask(__name__)

last_raw = "нет данных"
last_json = {}

@app.route('/', methods=['POST'])
def handler():
    global last_raw, last_json
    last_raw = request.get_data(as_text=True)
    last_json = request.get_json(silent=True) or {}
    return jsonify({"result": True}), 200

@app.route('/debug', methods=['GET'])
def debug():
    return jsonify({
        "raw": last_raw[:1000],
        "json": last_json,
        "content_type": str(request.content_type),
        "method": request.method
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)

from flask import Flask, request, render_template_string
import boto3
import PyPDF2
import io
import json
import re
from datetime import datetime

app = Flask(__name__)

# AWS SQS setup
sqs = boto3.client('sqs',
                   region_name='us-east-1',
                   aws_access_key_id='AKIAYFRFK5RX5BLLDPLZ',
                   aws_secret_access_key='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')  # 🔒 Consider using env vars

queue_url = 'https://sqs.us-east-1.amazonaws.com/561645284463/health-alert-queue'

UPLOAD_FORM = '''
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>SkyVitals Clinic App</title>
  <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-100 text-gray-800 font-sans">
  <header class="bg-blue-700 text-white p-6 shadow-md">
    <div class="container mx-auto flex flex-col items-center">
      <h1 class="text-4xl font-extrabold mb-1">SkyVitals Clinic App</h1>
      <p class="text-lg font-light text-center">Upload your health report to receive fast, personalized assessments and recommendations.</p>
    </div>
  </header>

  <main class="container mx-auto mt-16 px-4">
    <div class="bg-white rounded-xl shadow-lg p-8 max-w-lg mx-auto">
      <div class="mb-4 text-center">
        <h2 class="text-2xl font-semibold mb-2">Analyze Your Medical Report</h2>
        <p class="text-sm text-gray-600">Your data is encrypted and stays confidential. Your health deserves attention, let’s get started!</p>
      </div>

      <form action="/" method="post" enctype="multipart/form-data" class="space-y-6">
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-2">Choose a PDF File</label>
          <input type="file" name="pdf" accept=".pdf"
            class="block w-full text-sm text-gray-900 border border-gray-300 rounded-lg cursor-pointer bg-gray-50 focus:outline-none" />
        </div>

        <div class="text-center">
          <input type="submit" value="Upload & Analyze"
            class="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-lg shadow-md transition duration-150" />
        </div>
      </form>

      <div class="mt-6 text-center text-xs text-gray-500">
        <p>We never share your reports without your permission.</p>
        <a href="/privacy-policy" class="underline text-blue-600 hover:text-blue-800">View Privacy Policy</a>
      </div>
    </div>

    {{ result_block|safe }}
  </main>

  <footer class="mt-12 text-center text-sm text-gray-500">
    &copy; 2025 SkyVitals Clinic. All rights reserved.
  </footer>
</body>
'''

def extract_fields(text):
    fields = {
        "patient_id": None,
        "test_type": None,
        "value": None,
        "unit": None,
        "timestamp": None
    }

    patterns = {
        "patient_id": r"Patient ID\s*[:\-]\s*([A-Za-z0-9]+)",
        "test_type": r"Test Type\s*[:\-]\s*([A-Za-z ]+)",
        "value_unit": r"Test Value\s*[:\-]\s*([0-9.]+)\s*([a-zA-Z/%]+)",
        "timestamp": r"(?:Test Time|Timestamp)\s*[:\-]\s*([\d\-T:\.Z]+)"
    }

    for key in ["patient_id", "test_type", "timestamp"]:
        match = re.search(patterns[key], text, re.IGNORECASE)
        if match:
            fields[key] = match.group(1).strip()

    match = re.search(patterns["value_unit"], text, re.IGNORECASE)
    if match:
        try:
            fields["value"] = float(match.group(1).strip())
        except ValueError:
            fields["value"] = None
        fields["unit"] = match.group(2).strip()

    if not fields["timestamp"] or fields["timestamp"].lower() == "now":
        fields["timestamp"] = datetime.utcnow().isoformat() + "Z"

    return fields

def generate_insights(report):
    insights = {
        "status": "Normal",
        "message": "Your test results are within normal range.",
        "recommendation": []
    }

    test_type = report["test_type"].lower()
    value = report["value"]

    if test_type == "hemoglobin" and value < 12:
        insights["status"] = "Critical"
        insights["message"] = "Your Hemoglobin level is below the normal threshold."
        insights["recommendation"] = [
            "Eat iron-rich foods: spinach, lentils, red meat",
            "Include vitamin C sources to boost absorption",
            "Avoid coffee/tea right after meals"
        ]
    elif test_type == "blood sugar" and value > 200:
        insights["status"] = "Critical"
        insights["message"] = "Your blood sugar level is too high."
        insights["recommendation"] = [
            "Avoid sugary drinks and snacks",
            "Exercise regularly",
            "Consult a doctor for medication options"
        ]

    return insights

@app.route('/', methods=['GET', 'POST'])
def upload_pdf():
    if request.method == 'POST':
        if 'pdf' not in request.files:
            return 'No file part', 400

        file = request.files['pdf']
        if file.filename == '':
            return 'No selected file', 400

        if file:
            try:
                reader = PyPDF2.PdfReader(io.BytesIO(file.read()))
                text = ''.join(page.extract_text() for page in reader.pages if page.extract_text())
            except Exception as e:
                return f'Failed to read PDF: {str(e)}', 500

            if len(text) > 200000:
                return 'PDF too large to send to SQS', 400

            report = extract_fields(text)

            if not report["patient_id"] or not report["test_type"] or report["value"] is None:
                return f'Invalid or incomplete data extracted from PDF: {json.dumps(report)}', 400

            try:
                response = sqs.send_message(
                    QueueUrl=queue_url,
                    MessageBody=json.dumps(report)
                )
                insights = generate_insights(report)
                HTML_RESULT_TEMPLATE = '''
                <div class="bg-white rounded-xl shadow-lg p-6 mt-8 max-w-xl mx-auto">
                  <h2 class="text-2xl font-bold text-blue-700 mb-4">Report Summary</h2>
                  <ul class="text-gray-800 space-y-1">
                    <li><strong>Patient ID:</strong> {{ report.patient_id }}</li>
                    <li><strong>Test Type:</strong> {{ report.test_type }}</li>
                    <li><strong>Value:</strong> {{ report.value }} {{ report.unit }}</li>
                    <li><strong>Status:</strong> <span class="{% if insights.status == 'Critical' %}text-red-600{% else %}text-green-600{% endif %} font-semibold">{{ insights.status }}</span></li>
                  </ul>

                  {% if insights.status == 'Critical' %}
                  <div class="mt-4 p-4 bg-red-100 border-l-4 border-red-600 text-red-800 rounded">
                    <strong>Alert Sent:</strong> Your result is critical. A clinician has been notified via our health alert system.
                  </div>
                  {% endif %}

                  <div class="mt-4">
                    <p class="font-semibold text-gray-700">Insight:</p>
                    <p class="text-gray-600">{{ insights.message }}</p>
                  </div>

                  {% if insights.recommendation %}
                  <div class="mt-4">
                    <p class="font-semibold text-gray-700">Dietary Recommendations:</p>
                    <ul class="list-disc list-inside text-gray-600">
                      {% for rec in insights.recommendation %}
                      <li>{{ rec }}</li>
                      {% endfor %}
                    </ul>
                  </div>
                  {% endif %}

                  <div class="mt-6 text-sm text-gray-600">
                    <p><strong>What happens next?</strong></p>
                    <ul class="list-disc list-inside">
                      <li>A clinician will review your result.</li>
                      <li>If necessary, you'll be contacted shortly.</li>
                      <li>In case of symptoms, seek immediate care.</li>
                    </ul>
                    <p class="mt-2">Alert time: {{ report.timestamp }}</p>
                  </div>
                </div>
                '''
                result_block = render_template_string(HTML_RESULT_TEMPLATE, report=report, insights=insights)
                return render_template_string(UPLOAD_FORM, result_block=result_block)
            except Exception as e:
                return f'Failed to send to SQS: {str(e)}', 500

    return render_template_string(UPLOAD_FORM)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=True)

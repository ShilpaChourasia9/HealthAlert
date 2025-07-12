from flask import Flask, request, render_template_string
import boto3
import os
import PyPDF2
import io
import json
import re
from datetime import datetime

app = Flask(__name__)

# AWS SQS setup
sqs = boto3.client('sqs', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
queue_url = os.environ.get('SQS_QUEUE_URL')

S3_BUCKET = os.environ.get('S3_REPORT_BUCKET', 'health-alert-reports')
s3 = boto3.client('s3')

REPORT_FORM = '''
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
      <p class="text-lg font-light text-center">Select a report to analyze and receive personalized insights.</p>
    </div>
  </header>
  <main class="container mx-auto mt-8 px-4 max-w-3xl">
    <div class="bg-white rounded-xl shadow-lg p-8 w-full">
      <div class="mb-4">
        <h2 class="text-2xl font-semibold mb-4">Analyze Your Health Report</h2>
        <p class="text-sm text-gray-600 mb-6">Select a health report from the list below to view its summary and get personalized insights.</p>
        <form action="/" method="post" class="space-y-6">
          <div>
            <label class="block text-base font-medium text-gray-800 mb-2">Select Report</label>
            <select name="selected_report" class="block w-full border border-gray-300 rounded-lg p-2">
              {% for report in reports %}
                <option value="{{ report }}">{{ report }}</option>
              {% endfor %}
            </select>
            <div class="mt-4">
              <h3 class="text-sm font-medium text-gray-700 mb-2 mt-6">Need to review before analyzing? Preview your uploaded reports:</h3>
              <ul class="list-disc pl-6 space-y-2 text-sm text-blue-700 mt-2">
              {% for report in reports %}
                <li class="leading-relaxed"><a href="{{ signed_urls[report] }}" target="_blank" class="hover:underline">🔍 {{ report }}</a></li>
              {% endfor %}
            </ul>
              </div>
          <div class="text-center mt-6">
            <input type="submit" value="Analyze Report" class="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-lg shadow-md transition duration-150" />
          </div>
        </form>
      </div>
    </div>
    {{ result_block|safe }}
  </main>
  <footer class="mt-8 text-center text-sm text-gray-500 pb-6">
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
def select_report():
    from urllib.parse import quote
    from datetime import timedelta
    from markupsafe import Markup
    response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix='')
    reports = [obj['Key'] for obj in response.get('Contents', []) if obj['Key'].endswith('.pdf')]
    signed_urls = {
        key: s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': key}, ExpiresIn=3600)
        for key in reports
    }

    if request.method == 'POST':
        selected = request.form.get('selected_report')
        try:
            s3_obj = s3.get_object(Bucket=S3_BUCKET, Key=selected)
            file_bytes = s3_obj['Body'].read()
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            text = ''.join(page.extract_text() for page in reader.pages if page.extract_text())
        except Exception as e:
            return f'Failed to read PDF: {str(e)}', 500

        report = extract_fields(text)

        if not report["patient_id"] or not report["test_type"] or report["value"] is None:
            return f'Invalid or incomplete data extracted from PDF: {json.dumps(report)}', 400

        try:
            sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(report))
            insights = generate_insights(report)
            HTML_RESULT_TEMPLATE = '''
<div class="bg-white rounded-xl shadow-lg p-6 mt-8 max-w-xl mx-auto">
  <h3 class="text-xl font-semibold mb-4">Analysis Summary</h3>
  <p><strong>Patient ID:</strong> {{ report['patient_id'] }}</p>
  <p><strong>Test Type:</strong> {{ report['test_type'] }}</p>
  <p><strong>Value:</strong> {{ report['value'] }} {{ report['unit'] }}</p>
  <p><strong>Timestamp:</strong> {{ report['timestamp'] }}</p>
  <hr class="my-4">
  <p class="text-lg font-semibold">Status: <span class="{{ 'text-red-600' if insights['status'] == 'Critical' else 'text-green-600' }}">{{ insights['status'] }}</span></p>
  <p class="mb-2">{{ insights['message'] }}</p>
  {% if insights['recommendation'] %}
    <h4 class="font-medium">Recommendations:</h4>
    <ul class="list-disc pl-6 text-sm text-gray-700">
      {% for rec in insights['recommendation'] %}
        <li>{{ rec }}</li>
      {% endfor %}
    </ul>
  {% endif %}
  {% if insights['status'] == 'Critical' %}
  <div class="mt-6 text-sm text-red-700">
    <p class="font-semibold"><strong>Alert Sent:</strong> Your result is critical. A clinician has been notified via our health alert system.</p>
    <div class="mt-4 text-gray-600">
      <p><strong>What happens next?</strong></p>
      <ul class="list-disc list-inside">
        <li>A clinician will review your result.</li>
        <li>If necessary, you'll be contacted shortly.</li>
        <li>In case of symptoms, seek immediate care.</li>
      </ul>
      <p class="mt-2">Alert time: {{ report.timestamp }}</p>
    </div>
  </div>
  {% endif %}
</div>'''
            result_block = render_template_string(HTML_RESULT_TEMPLATE, report=report, insights=insights)
            return render_template_string(REPORT_FORM, reports=reports, result_block=result_block, signed_urls=signed_urls)
        except Exception as e:
            return f'Failed to send to SQS: {str(e)}', 500

    return render_template_string(REPORT_FORM, reports=reports, result_block='', signed_urls=signed_urls)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=True)

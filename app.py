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
  <main class="container mx-auto mt-16 px-4">
    <div class="bg-white rounded-xl shadow-lg p-8 max-w-lg mx-auto">
      <div class="mb-4 text-center">
        <h2 class="text-2xl font-semibold mb-2">Choose a Report</h2>
        <form action="/" method="post" class="space-y-6">
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-2">Available Reports</label>
            <select name="selected_report" class="block w-full border border-gray-300 rounded-lg p-2">
              {% for report in reports %}
                <option value="{{ report }}">{{ report }}</option>
              {% endfor %}
            </select>
            <div class="mt-2 text-right">
              {% for report in reports %}
                <a href="{{ signed_urls[report] }}" target="_blank" class="text-sm text-blue-600 hover:underline block">🔍 Preview {{ report }}</a>
              {% endfor %}
            </div>
          </div>
          <div class="text-center">
            <input type="submit" value="Analyze Report" class="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-lg shadow-md transition duration-150" />
          </div>
        </form>
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
def select_report():
    from urllib.parse import quote
    from datetime import timedelta
    from flask import Markup
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
            HTML_RESULT_TEMPLATE = '''<div class="bg-white rounded-xl shadow-lg p-6 mt-8 max-w-xl mx-auto">...same as before...</div>'''
            result_block = render_template_string(HTML_RESULT_TEMPLATE, report=report, insights=insights)
            return render_template_string(REPORT_FORM, reports=reports, result_block=result_block, signed_urls=signed_urls)
        except Exception as e:
            return f'Failed to send to SQS: {str(e)}', 500

    return render_template_string(REPORT_FORM, reports=reports, result_block='', signed_urls=signed_urls)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=True)

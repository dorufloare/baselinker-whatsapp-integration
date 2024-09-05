import requests  # type: ignore
import json
import time
import base64
from twilio.rest import Client  # type: ignore
import datetime
from dotenv import load_dotenv # type: ignore
import os
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
from twilio.base.exceptions import TwilioRestException

load_dotenv()

TEST_MODE = True

def format_json(my_json):
    formatted_json = json.dumps(my_json, indent=4, ensure_ascii=False)
    return formatted_json

def estimated_delivery_time(unix_timestamp):
    date_time = datetime.datetime.fromtimestamp(unix_timestamp)

    # Check the hour of the timestamp
    if date_time.hour < 15:
        days_to_add = 1
    else:
        days_to_add = 2

    current_day = date_time.weekday()  
    
    if current_day + days_to_add > 4:  
        extra_days = 2  
    else:
        extra_days = 0

    new_date = date_time + datetime.timedelta(days=days_to_add + extra_days)
    return new_date.strftime('%Y-%m-%d')

# Google drive

gauth = GoogleAuth()

gauth.LoadCredentialsFile("mycreds.txt")

if gauth.credentials is None:
    gauth.LocalWebserverAuth()  
elif gauth.access_token_expired:
    gauth.Refresh()
else:
    gauth.Authorize()
gauth.SaveCredentialsFile("mycreds.txt")

drive = GoogleDrive(gauth)

def get_url(filename):
   
    file = drive.CreateFile({'title': filename})
    file.SetContentFile(filename)
    file.Upload()

    file.InsertPermission({
        'type': 'anyone',
        'value': 'anyone',
        'role': 'reader'
    })


    public_url = file['alternateLink']
    print(public_url);
    return public_url


# Time constants
seconds_per_hour = 3600
update_interval = 24           
unix_time_since_last_update = int(time.time()) - update_interval * seconds_per_hour

# Twilio
account_sid = os.getenv('TWILIO_ACCOUNT_SID')
auth_token = os.getenv('TWILIO_AUTH_TOKEN')
client = Client(account_sid, auth_token)

# Baselinker API -> getting all the orders
url = "https://api.baselinker.com/connector.php"

headers = {
    "X-BLToken": os.getenv('X_BLTOKEN'),
    "Content-Type": 'application/x-www-form-urlencoded'
}

data = {
    "method": 'getOrders',
    "parameters": json.dumps({
        "date_from": unix_time_since_last_update,
        "get_unconfirmed_orders": True
    })
}

response = requests.post(url, headers=headers, data=data)
response_json = response.json()

# Only keeping the personal orders
personal_orders = [
    order for order in response_json.get("orders", []) 
    if order.get("order_source") == "personal"
]

print(f"Status Code: {response.status_code}")
print(format_json(personal_orders))

# For each order we need the invoice
for order in personal_orders:
    order_id = order.get("order_id")
  
    invoice_data = {
        "method": 'getInvoices',
        "parameters": json.dumps({
            "order_id": order_id,
            "get_external_invoices": True
        })
    }

    
    # Get the invoice ID
    invoice_response = requests.post(url, headers=headers, data=invoice_data)
    invoice_json = invoice_response.json()

    # Check if 'invoices' exists and has at least one item
    if invoice_json.get('invoices') and len(invoice_json['invoices']) > 0:
        invoice_id = invoice_json['invoices'][0].get('invoice_id')
    else:
        print(f"No invoice found for Order ID: {order_id}. Skipping to the next order.")
        continue

    invoice_file_data = {
        "method": 'getInvoiceFile',
        "parameters": json.dumps({
            "invoice_id": invoice_id
        })
    }

    # Get the invoice pdf from the request and save it
    
    invoice_file_response = requests.post(url, headers=headers, data=invoice_file_data)
    invoice_file_data_base64 = invoice_file_response.json().get('invoice')
    invoice_pdf_data = base64.b64decode(invoice_file_data_base64)

    output_pdf_path = f"factura_{invoice_id}.pdf"

    with open(output_pdf_path, "wb") as pdf_file:
        pdf_file.write(invoice_pdf_data)
    
    pdf_url = get_url(output_pdf_path);

    # Get the order page

    order_page_url = order.get('order_page')

    # Get the cargus AWB

    cargus_awb_data = {
        "method": 'getOrderPackages',
        "parameters": json.dumps({
            "order_id": order_id
        })
    }

    cargus_awb_response = requests.post(url, headers=headers, data=cargus_awb_data)
    cargus_awb_packages = cargus_awb_response.json().get('packages')
    
    package_numbers = ', '.join(package.get('courier_package_nr', '') for package in cargus_awb_packages)

    print(package_numbers)

    # Get the estimated delivery

    order_time_unix = order.get("date_add")
    estimated_delivery = estimated_delivery_time(order_time_unix)

    # Get the client phone number

    client_phone_number = order.get("phone")

    # Write message body

    message_body = (
        "🚚 Comanda expediata :) \n\n"
        "Detalii colet: \n\n"
        f"AWB: {package_numbers} \n"
        f"Livrare estimata: {estimated_delivery} \n"
        "Plata: ramburs\n\n"
        f"Factura: {pdf_url}\n\n"
        "Spor la lucru!"
    )

    #print(message_body);

    # If the test mode is enabled, we send the message to my personal phone number
    # Else we are sending it to the client

    recipient = os.getenv('PERSONAL_PHONE_NUMBER') if TEST_MODE else ''
    

    # Try sending the WhatsApp message
    message = client.messages.create(
        from_='+18564741965',
        body=message_body,
        to=recipient
    )
    print(f"WhatsApp message sent with SID: {message.sid}")

   
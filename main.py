import requests  
import json
import time
import base64
from twilio.rest import Client 
import datetime
from dotenv import load_dotenv 
import os
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
from twilio.base.exceptions import TwilioRestException

DELIVERED_ORDER_STATUS_ID = 20507

load_dotenv()

TEST_MODE = False

def format_json(my_json):
    formatted_json = json.dumps(my_json, indent=4, ensure_ascii=False)
    return formatted_json

def estimated_delivery_time(unix_timestamp):
    date_time = datetime.datetime.fromtimestamp(unix_timestamp)

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

def load_processed_orders():
    if not os.path.exists('orders.txt'):
        return set()
    with open('orders.txt', 'r') as f:
        return set(f.read().splitlines())

def save_processed_order(order_id):
    with open('orders.txt', 'a') as f:
        f.write(f"{order_id}\n")
        print(f"{order_id} SAVED")

# Google Drive authentication
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

def get_url(filename, folder_id):
    file = drive.CreateFile({'title': filename, 'parents': [{'id': folder_id}]})
    file.SetContentFile(filename)
    file.Upload()

    file.InsertPermission({
        'type': 'anyone',
        'value': 'anyone',
        'role': 'reader'
    })

    public_url = file['alternateLink']
    print(public_url)
    return public_url

# Time constants
seconds_per_hour = 3600
update_interval = 48
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

#print(f"Status Code: {response.status_code}")
#print(format_json(personal_orders))

processed_orders = load_processed_orders()

# Folder ID for 'Maide invoices' (Replace with actual folder ID)
folder_id = '1TSiJuppYSjj-IztAgmSsGTKt0B0cubbN'

for order in personal_orders:
    order_id = order.get("order_id")
    
    if str(order_id) in processed_orders:
        print(f"Order ID {order_id} already processed. Skipping.")
        continue
    
    order_status_id = order.get("order_status_id")
    if order_status_id != DELIVERED_ORDER_STATUS_ID:
        print(f"Order ID {order_id} not shipped yet. Skipping")
        continue

    invoice_data = {
        "method": 'getInvoices',
        "parameters": json.dumps({
            "order_id": order_id,
            "get_external_invoices": True
        })
    }

    invoice_response = requests.post(url, headers=headers, data=invoice_data)
    invoice_json = invoice_response.json()

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

    invoice_file_response = requests.post(url, headers=headers, data=invoice_file_data)
    invoice_file_data_base64 = invoice_file_response.json().get('invoice')
    invoice_pdf_data = base64.b64decode(invoice_file_data_base64)

    output_pdf_path = f"factura_{invoice_id}.pdf"

    with open(output_pdf_path, "wb") as pdf_file:
        pdf_file.write(invoice_pdf_data)
    
    pdf_url = get_url(output_pdf_path, folder_id)

    cargus_awb_data = {
        "method": 'getOrderPackages',
        "parameters": json.dumps({
            "order_id": order_id
        })
    }

    cargus_awb_response = requests.post(url, headers=headers, data=cargus_awb_data)
    cargus_awb_packages = cargus_awb_response.json().get('packages')

    package_numbers = ', '.join(package.get('courier_package_nr', '') for package in cargus_awb_packages)

    order_time_unix = order.get("date_add")
    estimated_delivery = estimated_delivery_time(order_time_unix)
    client_phone_number = order.get("phone")

    message_body = (
        "🚚 Comanda expediata :) \n\n"
        "Detalii colet: \n\n"
        f"Livrare estimata: {estimated_delivery} \n"
        "Plata: ramburs\n\n"
        f"Factura: {pdf_url}\n\n"
        f"AWB: {package_numbers}\n\n"
        "Spor la lucru!"
    )

    recipient = os.getenv('PERSONAL_PHONE_NUMBER') if TEST_MODE else client_phone_number

    try:
        '''
        message = client.messages.create(
            from_='+18564741965',
            body=message_body,
            to=recipient
        )
        print(f"Message sent with SID: {message.sid}")
        print(order_id)'''
        save_processed_order(order_id)
        
        
    except TwilioRestException as e:
        print(f"Failed to send message to {recipient}: {e}")

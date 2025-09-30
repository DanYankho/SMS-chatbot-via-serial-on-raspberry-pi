import serial
import threading
import time
import csv
from datetime import datetime

# --- CONFIGURATION ---
SERIAL_PORT = "/dev/ttyUSB2"  # your SMS modem port
BAUD_RATE = 115200
CSV_FILE = "vsla_reports.csv"
SESSION_TIMEOUT = 40 * 60  # 40 minutes inactivity
CHECK_INTERVAL = 5  # seconds between SMS checks

# --- GROUP NAMES ---
groupNames = [
    "",  # index 0 (not used)
    "Mkama H&C Women's Group",
    "Chilulu H&C Women's Group",
    "Chimnyanga H&C Women's Group",
    "Tchesamu H&C Women's Group",
    "Emcizini H&C Women's Group",
    "Mkamaumoza H&C Women's Group",
    "Kabira H&C Women's Group",
    "Chambizi H&C Women's Group",
    "Kanjuchi H&C Womens Savings",
    "Tiphunzire nawo Women's Group",
    "Kanyenda Bank Mkhonde",
    "Chivunguti Bank Mkhonde",
    "Ukwe Bank Mkhonde",
    "Mvunguti Bank Mkhonde",
    "Chimbiya H&C Women's Group",
    "Lisasadzi Bank Mkhonde",
    "Kanjeza H&C Women's Group"
]

# --- GLOBALS ---
sessions = {}  # phone -> session dict
sessions_lock = threading.Lock()
csv_lock = threading.Lock()


# --- UTILITY FUNCTIONS ---
def send_at_command(ser, command, wait=1):
    ser.write((command + "\r").encode())
    time.sleep(wait)
    resp = ser.read_all().decode(errors="ignore")
    return resp


def send_sms(ser, phone, message):
    send_at_command(ser, 'AT+CMGF=1')  # text mode
    # Ensure phone has + prefix
    if not phone.startswith("+"):
        phone = "+" + phone
    # Prepare SMS sending
    ser.write(f'AT+CMGS="{phone}"\r'.encode())
    time.sleep(0.5)
    ser.write((message + chr(26)).encode())  # Ctrl+Z to send
    time.sleep(2)
    resp = ser.read_all().decode(errors="ignore")
    return "OK" in resp or ">" in resp


def read_sms(ser):
    send_at_command(ser, 'AT+CMGF=1')  # text mode
    resp = send_at_command(ser, 'AT+CMGL="REC UNREAD"', wait=2)
    messages = []
    lines = resp.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("+CMGL:"):
            parts = line.split(",")
            index = parts[0].split(":")[1].strip()
            phone = parts[2].replace('"', '').strip()
            content = lines[i + 1].strip()
            messages.append({"index": index, "phone": phone, "content": content})
            i += 1
        i += 1
    return messages


def mark_sms_as_read(ser, index):
    send_at_command(ser, f'AT+CMGD={index},1')  # mark as read


def save_to_csv(data):
    with csv_lock:
        file_exists = False
        try:
            with open(CSV_FILE, "r"):
                file_exists = True
        except FileNotFoundError:
            pass

        with open(CSV_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "Phone", "Group", "Amount Saved", "Attendance",
                    "Social Fund", "Loans Taken", "Loans Repaid", "Timestamp"
                ])
            writer.writerow([
                data["phone"],
                data["group_name"],
                data["amount_saved"],
                data["attendance"],
                data["social_fund"],
                data["loans_taken"],
                data["loans_repaid"],
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ])


# --- SESSION MANAGEMENT ---
def handle_response(phone, message):
    # Check if session exists, else create
    with sessions_lock:
        session = sessions.get(phone)
        if not session:
            session = {
                "step": 0,
                "data": {},
                "last_active": time.time()
            }
            sessions[phone] = session

    session["last_active"] = time.time()
    step = session["step"]
    msg = message.strip()
    reply = ""

    try:
        if step == 0:  # ask for group number
            if "vsla" in msg.lower():
                reply = "Welcome! Choose your group number (1-17):"
                session["step"] = 1
            else:
                reply = "Please start your message with 'VSLA' to report."
        elif step == 1:  # group number
            if msg.isdigit() and 1 <= int(msg) <= 17:
                group_id = int(msg)
                session["data"]["group_name"] = groupNames[group_id]
                reply = "Enter amount saved:"
                session["step"] = 2
            else:
                reply = "Invalid group number. Choose 1-17:"
        elif step == 2:  # amount saved
            if msg.isdigit():
                session["data"]["amount_saved"] = msg
                reply = "Enter attendance:"
                session["step"] = 3
            else:
                reply = "Invalid number. Enter amount saved:"
        elif step == 3:  # attendance
            if msg.isdigit():
                session["data"]["attendance"] = msg
                reply = "Enter social fund amount:"
                session["step"] = 4
            else:
                reply = "Invalid number. Enter attendance:"
        elif step == 4:  # social fund
            if msg.isdigit():
                session["data"]["social_fund"] = msg
                reply = "Enter loans taken:"
                session["step"] = 5
            else:
                reply = "Invalid number. Enter social fund amount:"
        elif step == 5:  # loans taken
            if msg.isdigit():
                session["data"]["loans_taken"] = msg
                reply = "Enter loans repaid:"
                session["step"] = 6
            else:
                reply = "Invalid number. Enter loans taken:"
        elif step == 6:  # loans repaid
            if msg.isdigit():
                session["data"]["loans_repaid"] = msg
                session["data"]["phone"] = phone
                save_to_csv(session["data"])
                reply = "Thank you! Your report has been received."
                with sessions_lock:
                    del sessions[phone]
            else:
                reply = "Invalid number. Enter loans repaid:"
    except Exception as e:
        reply = "Error processing input. Please try again."

    return reply


# --- THREAD WORKER ---
def process_message(ser, msg):
    phone = msg["phone"]
    content = msg["content"]
    reply = handle_response(phone, content)
    if send_sms(ser, phone, reply):
        mark_sms_as_read(ser, msg["index"])
        print(f"Replied to {phone}: {reply}")
    else:
        print(f"Failed to send to {phone}")


# --- CLEANUP INACTIVE SESSIONS ---
def cleanup_sessions():
    while True:
        now = time.time()
        with sessions_lock:
            inactive = [p for p, s in sessions.items() if now - s["last_active"] > SESSION_TIMEOUT]
            for phone in inactive:
                del sessions[phone]
        time.sleep(60)


# --- MAIN LOOP ---
def main():
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    threading.Thread(target=cleanup_sessions, daemon=True).start()
    print("VSLA SMS bot started...")

    while True:
        try:
            messages = read_sms(ser)
            for msg in messages:
                threading.Thread(target=process_message, args=(ser, msg), daemon=True).start()
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print("Error:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()

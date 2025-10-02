#!/usr/bin/env python3
import serial
import threading
import time
import csv
from datetime import datetime

# --- CONFIG ---
SERIAL_PORT = "/dev/ttyUSB0"
BAUD_RATE = 115200
CSV_FILE = "vsla_reports.csv"
SESSION_TIMEOUT = 40 * 60   # 40 minutes inactivity
CHECK_INTERVAL = 5          # poll every 5s
TIME_FORMAT = "%Y-%m-%d, %H:%M:%S"

# --- GROUP NAMES ---
groupNames = [
    "",  # index 0 unused
    "Mkama",
    "Chilulu",
    "Chimnyanga",
    "Tchesamu",
    "Emcizini",
    "Mkamaumoza",
    "Kabira",
    "Chambizi",
    "Kanjuchi",
    "Tiphunzire",
    "Kanyenda",
    "Chivunguti",
    "Ukwe",
    "Mvunguti",
    "Chimbiya",
    "Lisasadzi",
    "Kanjeza"
]

# --- FOLLOW-UP QUESTIONS ---
followup_questions = {
    "english": [
        "Number of members present today",
        "Amount of shares collected today",
        "Amount of social fund collected today",
        "Amount borrowed today",
        "Amount repaid today",
        "Interest paid today",
        "Amount paid for penalties (latecomers and absentees)"
    ],
    "chichewa": [
        "Anthu omwe anabwera lero",
        "Ndalama za ma sheya zotoleledwa lero",
        "Ndalama zadzidzidzi zatoleredwa lero",
        "Ndalama zomwe zabwerekedwa",
        "Ndalama zomwe zabwenzedwa",
        "Ndalama za chiongola dzanja yomwe yapelekedwa lero",
        "Ndalama za chilango zomwe zaperekedwa (ochedwa ndi ojomba)"
    ],
    "tumbuka": [
        "Nambala ya ŵanthu agha wangwiza mwahuno ku gulu",
        "Ndalama ya masheya iyo yasonkheka yamwahuno",
        "Ndalama ya zizizi iyo yasonkheka yamwahuno",
        "Ndalama iyo yabwerekeka mwahuno",
        "Ndalama za ngongoli izo zawezgeka",
        "Ndalama ya interest iyo yasonkheka mwahuno",
        "Ndalama iyo yasonkheka lero mu vilango/kubuda"
    ]
}

LANGUAGE_MENU = "Choose your language:\n1. English\n2. Chichewa\n3. Chitumbuka"

# --- SPLIT GROUP LISTS (two-part for sending) ---
group_list_parts = {
    "english": (
        "Choose your group number from the list below:\n\n"
        "1.  Mkama\n2.  Chilulu\n3.  Chimnyanga\n4.  Tchesamu\n5.  Emcizini\n6.  Mkamaumoza\n7.  Kabira\n8.  Chambizi",
        "9.  Kanjuchi\n10. Tiphunzire\n11. Kanyenda\n12. Chivunguti\n13. Ukwe\n14. Mvunguti\n15. Chimbiya\n16. Lisasadzi\n17. Kanjeza"
    ),
    "chichewa": (
        "Sankhani nambala la gulu yanu:\n\n"
        "1.  Mkama\n2.  Chilulu\n3.  Chimnyanga\n4.  Tchesamu\n5.  Emcizini\n6.  Mkamaumoza\n7.  Kabira\n8.  Chambizi",
        "9.  Kanjuchi\n10. Tiphunzire\n11. Kanyenda\n12. Chivunguti\n13. Ukwe\n14. Mvunguti\n15. Chimbiya\n16. Lisasadzi\n17. Kanjeza"
    ),
    "tumbuka": (
        "Sankhani nambala la gulu yanu:\n\n"
        "1.  Mkama\n2.  Chilulu\n3.  Chimnyanga\n4.  Tchesamu\n5.  Emcizini\n6.  Mkamaumoza\n7.  Kabira\n8.  Chambizi",
        "9.  Kanjuchi\n10. Tiphunzire\n11. Kanyenda\n12. Chivunguti\n13. Ukwe\n14. Mvunguti\n15. Chimbiya\n16. Lisasadzi\n17. Kanjeza"
    )
}

# --- GLOBALS ---
sessions = {}
sessions_lock = threading.Lock()
csv_lock = threading.Lock()
SEND_LOCK = threading.Lock()

# --- SERIAL / AT helpers ---
def send_at_command(ser, command, wait=0.8):
    with SEND_LOCK:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        ser.write((command + "\r").encode())
        time.sleep(wait)
        resp = ser.read_all().decode(errors="ignore")
        return resp

def send_sms(ser, phone, message, retries=3, wait_after=3.0):
    """Send SMS with auto-retry. Returns True if successfully sent."""
    for attempt in range(retries):
        try:
            with SEND_LOCK:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                ser.write(b'AT+CMGF=1\r')
                time.sleep(0.3)
                if not phone.startswith("+"):
                    phone_s = "+" + phone
                else:
                    phone_s = phone
                ser.write(f'AT+CMGS="{phone_s}"\r'.encode())
                time.sleep(0.5)
                ser.write(message.encode() + b'\x1A')
                time.sleep(wait_after)
                resp = ser.read_all().decode(errors="ignore")
                if "OK" in resp or "+CMGS" in resp:
                    print(f"\033[32m[{datetime.now().strftime(TIME_FORMAT)}] Sent to {phone}: {message}\033[0m")
                    return True
        except Exception as e:
            print(f"[send_sms] attempt {attempt+1} failed: {e}")
        time.sleep(1.0)
    print(f"[send_sms] FAILED to send after {retries} attempts: {message}")
    return False

# --- SMS read / delete ---
def read_sms(ser):
    out = send_at_command(ser, 'AT+CMGL="REC UNREAD"', wait=1.5)
    msgs = []
    lines = out.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("+CMGL:"):
            parts = line.split(",")
            try:
                index = line.split(":")[1].split(",")[0].strip()
            except:
                index = parts[0].split(":")[1].strip()
            phone = parts[2].replace('"', '').strip() if len(parts) >= 3 else ""
            content = lines[i+1].strip() if (i+1)<len(lines) else ""
            msgs.append({"index": index, "phone": phone, "content": content})
            i += 1
        i += 1
    return msgs

def delete_sms(ser, index):
    send_at_command(ser, f'AT+CMGD={index}', wait=0.6)

# --- CSV save ---
def save_to_csv(phone, lang, group_name, answers):
    with csv_lock:
        file_exists = False
        try:
            with open(CSV_FILE,"r"):
                file_exists=True
        except FileNotFoundError:
            file_exists=False
        with open(CSV_FILE,"a",newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "Phone","Language","Group",
                    "Members","Shares","SocialFund",
                    "LoansTaken","LoansRepaid","Interest","Penalties",
                    "Timestamp"
                ])
            row = [
                phone, lang, group_name,
                answers[0] if len(answers)>0 else "",
                answers[1] if len(answers)>1 else "",
                answers[2] if len(answers)>2 else "",
                answers[3] if len(answers)>3 else "",
                answers[4] if len(answers)>4 else "",
                answers[5] if len(answers)>5 else "",
                answers[6] if len(answers)>6 else "",
                datetime.now().strftime(TIME_FORMAT)
            ]
            writer.writerow(row)

# --- MESSAGE HANDLER ---
def handle_message(ser, phone, text):
    text = text.strip()
    now = time.time()

    # Only start session if message contains "vsla" or session already exists
    session_exists = phone in sessions
    if not session_exists and "vsla" not in text.lower():
        print(f"\033[90m[{datetime.now().strftime(TIME_FORMAT)}] Ignored from {phone}: {text}\033[0m")
        return

    # Get or create session
    with sessions_lock:
        session = sessions.get(phone)
        if not session:
            session = {"step": -1, "lang": None, "answers": [], "group": None, "last_active": now}
            sessions[phone] = session
        session["last_active"] = now
        step = session["step"]
        lang = session["lang"]

    # Step -1: language selection
    if step == -1:
        if text == "1":
            lang = "english"
        elif text == "2":
            lang = "chichewa"
        elif text == "3":
            lang = "tumbuka"
        else:
            send_sms(ser, phone, LANGUAGE_MENU)
            return

        with sessions_lock:
            sessions[phone]["lang"] = lang
            sessions[phone]["step"] = 0
        p1, p2 = group_list_parts[lang]
        send_sms(ser, phone, p1)
        time.sleep(1.5)
        send_sms(ser, phone, p2)
        return

    # Step 0: group number
    if step == 0:
        if text.isdigit() and 1 <= int(text) <= 17:
            gid = int(text)
            group = groupNames[gid]
            with sessions_lock:
                sessions[phone]["group"] = group
                sessions[phone]["step"] = 1
            # send first follow-up question
            q = followup_questions[lang][0]
            send_sms(ser, phone, q)
            return
        else:
            # invalid, resend group list
            p1,p2 = group_list_parts[lang]
            send_sms(ser, phone, "Invalid group number. " + p1)
            time.sleep(1.5)
            send_sms(ser, phone, p2)
            return

    # Steps 1..7: follow-up answers
    if 1 <= step <= 7:
        if text.isdigit():
            with sessions_lock:
                sessions[phone]["answers"].append(text)
                sessions[phone]["step"] += 1
                next_step = sessions[phone]["step"]
                lang_now = sessions[phone]["lang"]
            if next_step <= 7:
                q = followup_questions[lang_now][next_step -1]
                send_sms(ser, phone, q)
                return
            else:
                # all done
                with sessions_lock:
                    answers = sessions[phone]["answers"][:]
                    group_name = sessions[phone].get("group","")
                    lang_now = sessions[phone]["lang"]
                    try:
                        del sessions[phone]
                    except KeyError:
                        pass
                save_to_csv(phone, lang_now, group_name, answers)
                send_sms(ser, phone, "Thank you! Your report has been received and saved.")
                return
        else:
            # invalid number, resend same question
            q = followup_questions[lang][step-1]
            send_sms(ser, phone, "Invalid number. " + q)
            return

# --- Worker ---
def process_message(ser, msg):
    phone = msg["phone"]
    content = msg["content"]
    print(f"\033[94m[{datetime.now().strftime(TIME_FORMAT)}] Received from {phone}: {content}\033[0m")
    try:
        handle_message(ser, phone, content)
    except Exception as e:
        print(f"[process_message] error handling {phone}: {e}")
    finally:
        try:
            delete_sms(ser, msg["index"])
        except Exception as e:
            print(f"[process_message] failed to delete sms {msg.get('index')}: {e}")

# --- Cleanup sessions ---
def cleanup_sessions():
    while True:
        now = time.time()
        with sessions_lock:
            expired = [p for p,s in sessions.items() if now - s.get("last_active",0) > SESSION_TIMEOUT]
            for p in expired:
                print(f"[cleanup] session expired for {p}")
                del sessions[p]
        time.sleep(60)

# --- MAIN ---
def main():
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    send_at_command(ser,"AT")
    send_at_command(ser,"AT+CMGF=1")
    threading.Thread(target=cleanup_sessions,daemon=True).start()
    print("VSLA SMS bot started (polling every {}s)...".format(CHECK_INTERVAL))

    while True:
        try:
            msgs = read_sms(ser)
            for m in msgs:
                threading.Thread(target=process_message,args=(ser,m),daemon=True).start()
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"[main] error: {e}")
            time.sleep(5)

if __name__=="__main__":
    main()

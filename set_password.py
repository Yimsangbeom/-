"""(비상용) 서버에서 비밀번호 직접 설정:  venv\\Scripts\\python set_password.py
평소에는 화면의 [비밀번호 변경] 을 쓰면 되고, 잊었을 때는 reset_password.bat 후 화면에서 다시 설정."""
import getpass
import os
import sys
import auth

data_dir = os.environ.get("OMR_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
p1 = getpass.getpass(f"[{auth.DEFAULT_USERNAME}] 새 비밀번호({auth.MIN_LEN}자 이상): ")
p2 = getpass.getpass("한 번 더 입력: ")
if p1 != p2:
    sys.exit("두 비밀번호가 다릅니다.")
try:
    uid = input(f"아이디 (엔터=기존/{auth.DEFAULT_USERNAME}): ").strip() or None
    auth.set_password(data_dir, p1, uid)
except ValueError as e:
    sys.exit(str(e))
print("비밀번호가 저장되었습니다.")

"""접속 비밀번호 설정/변경:  python set_password.py   (서버 재시작 불필요)"""
import getpass
import os
import sys
import auth

data_dir = os.environ.get("OMR_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
p1 = getpass.getpass("새 비밀번호(8자 이상): ")
p2 = getpass.getpass("한 번 더 입력: ")
if p1 != p2:
    sys.exit("두 비밀번호가 다릅니다.")
try:
    auth.set_password(data_dir, p1)
except ValueError as e:
    sys.exit(str(e))
print("비밀번호가 저장되었습니다.")

from datetime import datetime
messsage = """YT：ひさな
頻道：oga
ID：yuuu#7885 
日期：2021.10.8"""
data = {}
member_role = {
    "oga": 886211138818306049
}

def generate_membership_command(messsage):
    date = None
    role = None
    for s in messsage.split('\n'):
        tmp = s.split('：')
        if len(tmp) != 2:
            return None
        key, value = tmp
        if key == "頻道":
            value = value.lower()
            if value not in member_role.keys():
                return None
            role = member_role[value]
        elif key == "日期":
            try:
                date = datetime.strptime(value, "%Y/%m/%d")
            except:
                return None
            date = date.strftime("%Y/%m/%d %H:%M")
    if date and role:
        return f"?temprole id {date} {role}"
    return None

print(generate_membership_command(messsage))
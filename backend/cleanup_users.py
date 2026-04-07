from dotenv import load_dotenv
load_dotenv()
from core.database import users_col

# Delete all stuck unverified accounts
result = users_col().delete_many({"verified": False})
print(f"Deleted {result.deleted_count} unverified/stuck account(s) from MongoDB.")

all_users = list(users_col().find({}, {"_id": 0, "password": 0}))
print(f"Remaining verified accounts: {len(all_users)}")
for u in all_users:
    print(f"  - {u['username']} | {u['email']} | verified={u.get('verified')}")

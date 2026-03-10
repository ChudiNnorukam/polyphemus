import os
from dotenv import load_dotenv
load_dotenv('/opt/sigil/sigil/.env')
pct = float(os.getenv('BASE_BET_PCT', '0.10'))
mn = float(os.getenv('MIN_BET', '5'))
mx = float(os.getenv('MAX_BET', '50'))
bal = 108.76
bet = max(mn, min(mx, bal * pct))
print(f'BASE_BET_PCT={pct}, MIN_BET={mn}, MAX_BET={mx}')
print(f'Balance={bal}, Calculated bet={bet:.2f}')
print(f'With $1100: bet={max(mn, min(mx, 1100 * pct)):.2f}')

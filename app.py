import sqlite3
import json
import random
import string
import time
from pathlib import Path
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
DB_PATH = Path(__file__).parent / "skibidi.db"


# ===== БАЗА ДАННЫХ =====
def init_db():
    con = sqlite3.connect(DB_PATH)
    c = con.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            code TEXT PRIMARY KEY,
            name TEXT DEFAULT 'Скибиди-Мастер',
            balance INTEGER DEFAULT 0,
            energy INTEGER DEFAULT 1000,
            energy_max INTEGER DEFAULT 1000,
            energy_regen INTEGER DEFAULT 1,
            tap_power INTEGER DEFAULT 1,
            upgrades TEXT DEFAULT '{}',
            referrals TEXT DEFAULT '[]',
            referred_by TEXT,
            total_taps INTEGER DEFAULT 0,
            max_combo INTEGER DEFAULT 0,
            achievements TEXT DEFAULT '[]',
            current_skin TEXT DEFAULT 'default',
            owned_skins TEXT DEFAULT '["default"]',
            last_daily INTEGER DEFAULT 0,
            last_seen INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0
        )
    """)
    con.commit()
    con.close()


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def gen_code():
    while True:
        code = 'SKB-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        con = db()
        row = con.execute("SELECT code FROM users WHERE code = ?", (code,)).fetchone()
        con.close()
        if not row:
            return code


# ===== ИГРОВАЯ ЛОГИКА =====
UPGRADES = {
    'turbo':        {'type': 'tap',    'base': 100,      'mult': 1.6, 'effect': 1},
    'energy_max':   {'type': 'energy', 'base': 200,      'mult': 1.7, 'effect': 500},
    'energy_regen': {'type': 'regen',  'base': 500,      'mult': 1.8, 'effect': 1},
    'gen1':         {'type': 'passive','base': 300,      'mult': 1.5, 'effect': 1},
    'gen2':         {'type': 'passive','base': 3000,     'mult': 1.5, 'effect': 15},
    'gen3':         {'type': 'passive','base': 30000,    'mult': 1.5, 'effect': 200},
    'gen4':         {'type': 'passive','base': 250000,   'mult': 1.5, 'effect': 2500},
    'gen5':         {'type': 'passive','base': 3000000,  'mult': 1.5, 'effect': 30000},
    'gen6':         {'type': 'passive','base': 50000000, 'mult': 1.5, 'effect': 400000},
}

ACHIEVEMENTS = [
    {'id': 'first_tap',    'name': 'Первый тап',       'reward': 100,    'cond': lambda u: u['total_taps'] >= 1},
    {'id': 'tap_100',      'name': 'Ста клик',         'reward': 500,    'cond': lambda u: u['total_taps'] >= 100},
    {'id': 'tap_1000',     'name': 'Тапальщик-мастер', 'reward': 5000,   'cond': lambda u: u['total_taps'] >= 1000},
    {'id': 'tap_10000',    'name': 'Скибиди-легенда',  'reward': 50000,  'cond': lambda u: u['total_taps'] >= 10000},
    {'id': 'combo_5',      'name': 'Скибиди-комбо',    'reward': 1000,   'cond': lambda u: u['max_combo'] >= 5},
    {'id': 'first_friend', 'name': 'Первый друг',      'reward': 10000,  'cond': lambda u: len(u['referrals']) >= 1},
    {'id': 'five_friends', 'name': 'Пять друзей',      'reward': 50000,  'cond': lambda u: len(u['referrals']) >= 5},
    {'id': 'rich',         'name': 'Скибиди-богач',    'reward': 100000, 'cond': lambda u: u['balance'] >= 1_000_000},
]

SKINS = {
    'default': {'price': 0,         'name': 'Классика'},
    'gold':    {'price': 50000,     'name': 'Золотой'},
    'diamond': {'price': 500000,    'name': 'Алмазный'},
    'mythic':  {'price': 5000000,   'name': 'Мифический'},
    'cosmic':  {'price': 25000000,  'name': 'Космический'},
    'rainbow': {'price': 100000000, 'name': 'Радужный'},
}


def user_to_dict(row):
    return {
        'code': row['code'],
        'name': row['name'],
        'balance': row['balance'],
        'energy': row['energy'],
        'energy_max': row['energy_max'],
        'energy_regen': row['energy_regen'],
        'tap_power': row['tap_power'],
        'upgrades': json.loads(row['upgrades']),
        'referrals': json.loads(row['referrals']),
        'referred_by': row['referred_by'],
        'total_taps': row['total_taps'],
        'max_combo': row['max_combo'],
        'achievements': json.loads(row['achievements']),
        'current_skin': row['current_skin'],
        'owned_skins': json.loads(row['owned_skins']),
        'last_daily': row['last_daily'],
        'last_seen': row['last_seen'],
        'created_at': row['created_at'],
    }


def get_passive(user):
    base = 0
    for uid, tpl in UPGRADES.items():
        if tpl['type'] == 'passive':
            base += user['upgrades'].get(uid, 0) * tpl['effect']
    ref_bonus = 1 + len(user['referrals']) * 0.05
    return int(base * ref_bonus)


def upgrade_price(uid, level):
    tpl = UPGRADES[uid]
    return int(tpl['base'] * (tpl['mult'] ** level))


def check_achievements(con, user_dict):
    unlocked = list(user_dict['achievements'])
    new = []
    for ach in ACHIEVEMENTS:
        if ach['id'] in unlocked:
            continue
        if ach['cond'](user_dict):
            unlocked.append(ach['id'])
            user_dict['balance'] += ach['reward']
            new.append(ach)
    if new:
        con.execute("UPDATE users SET achievements = ?, balance = ? WHERE code = ?",
                    (json.dumps(unlocked), user_dict['balance'], user_dict['code']))
        con.commit()
        user_dict['achievements'] = unlocked
    return new


def apply_offline_income(user):
    now = int(time.time())
    last_seen = user['last_seen'] or now
    diff = min(now - last_seen, 3 * 3600)
    if diff < 60:
        return 0
    passive = get_passive(user)
    return int(passive * diff * 0.5)


# ===== МАРШРУТЫ =====
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    user_code = data.get('code')
    ref = data.get('ref')
    name = data.get('name') or 'Скибиди-Мастер'

    con = db()
    row = None
    if user_code:
        row = con.execute("SELECT * FROM users WHERE code = ?", (user_code,)).fetchone()

    if row is None:
        new_code = gen_code()
        now = int(time.time())
        con.execute("""
            INSERT INTO users (code, name, referred_by, created_at, last_seen)
            VALUES (?, ?, ?, ?, ?)
        """, (new_code, name, ref if ref else None, now, now))
        con.commit()
        row = con.execute("SELECT * FROM users WHERE code = ?", (new_code,)).fetchone()

    user = user_to_dict(row)

    if ref and user['referred_by'] is None and ref != user['code']:
        con.execute("UPDATE users SET referred_by = ? WHERE code = ?", (ref, user['code']))
        con.execute("UPDATE users SET balance = balance + 5000 WHERE code = ?", (user['code'],))
        parent = con.execute("SELECT * FROM users WHERE code = ?", (ref,)).fetchone()
        if parent:
            parent_dict = user_to_dict(parent)
            parent_dict['referrals'].append({
                'code': user['code'],
                'name': user['name'],
                'joined_at': int(time.time()),
            })
            con.execute("""
                UPDATE users SET referrals = ?, balance = balance + 25000 WHERE code = ?
            """, (json.dumps(parent_dict['referrals']), ref))
        con.commit()
        row = con.execute("SELECT * FROM users WHERE code = ?", (user['code'],)).fetchone()
        user = user_to_dict(row)

    offline = apply_offline_income(user)
    if offline > 0:
        con.execute("UPDATE users SET balance = balance + ? WHERE code = ?", (offline, user['code']))
        con.commit()
        user['balance'] += offline

    con.execute("UPDATE users SET last_seen = ? WHERE code = ?", (int(time.time()), user['code']))
    con.commit()
    con.close()

    return jsonify({'user': user, 'offline_income': offline, 'passive': get_passive(user)})


@app.route('/api/state', methods=['POST'])
def get_state():
    data = request.get_json() or {}
    code = data.get('code')
    if not code:
        return jsonify({'error': 'no code'}), 400

    con = db()
    row = con.execute("SELECT * FROM users WHERE code = ?", (code,)).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'not found'}), 404

    user = user_to_dict(row)
    offline = apply_offline_income(user)
    if offline > 0:
        con.execute("UPDATE users SET balance = balance + ? WHERE code = ?", (offline, code))
        con.commit()
        user['balance'] += offline

    con.execute("UPDATE users SET last_seen = ? WHERE code = ?", (int(time.time()), code))
    con.commit()
    con.close()

    return jsonify({'user': user, 'offline_income': offline, 'passive': get_passive(user)})


@app.route('/api/tap', methods=['POST'])
def tap():
    data = request.get_json() or {}
    code = data.get('code')
    taps = int(data.get('taps', 0))
    combo = int(data.get('combo', 1))
    if not code or taps <= 0:
        return jsonify({'error': 'bad request'}), 400

    taps = min(taps, 50)

    con = db()
    row = con.execute("SELECT * FROM users WHERE code = ?", (code,)).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'not found'}), 404

    user = user_to_dict(row)
    actual_taps = min(taps, user['energy'])
    if actual_taps <= 0:
        con.close()
        return jsonify({'error': 'no energy', 'user': user})

    base_tap = user['tap_power']
    for uid, tpl in UPGRADES.items():
        if tpl['type'] == 'tap':
            base_tap += user['upgrades'].get(uid, 0) * tpl['effect']

    if combo >= 20:   mult = 5
    elif combo >= 10: mult = 3
    elif combo >= 5:  mult = 2
    else:             mult = 1

    income = base_tap * actual_taps * mult

    new_balance = user['balance'] + income
    new_energy = max(0, user['energy'] - actual_taps)
    new_total_taps = user['total_taps'] + actual_taps
    new_max_combo = max(user['max_combo'], combo)

    con.execute("""
        UPDATE users SET balance = ?, energy = ?, total_taps = ?, max_combo = ?
        WHERE code = ?
    """, (new_balance, new_energy, new_total_taps, new_max_combo, code))
    con.commit()

    user['balance'] = new_balance
    user['energy'] = new_energy
    user['total_taps'] = new_total_taps
    user['max_combo'] = new_max_combo

    new_ach = check_achievements(con, user)
    con.close()

    return jsonify({
        'user': user,
        'passive': get_passive(user),
        'new_achievements': new_ach,
    })


@app.route('/api/upgrade', methods=['POST'])
def upgrade():
    data = request.get_json() or {}
    code = data.get('code')
    uid = data.get('upgrade_id')
    if not code or uid not in UPGRADES:
        return jsonify({'error': 'bad request'}), 400

    con = db()
    row = con.execute("SELECT * FROM users WHERE code = ?", (code,)).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'not found'}), 404

    user = user_to_dict(row)
    tpl = UPGRADES[uid]
    lvl = user['upgrades'].get(uid, 0)
    price = upgrade_price(uid, lvl)

    if user['balance'] < price:
        con.close()
        return jsonify({'error': 'no money', 'user': user})

    user['balance'] -= price
    user['upgrades'][uid] = lvl + 1

    if tpl['type'] == 'energy':
        user['energy_max'] += tpl['effect']
    elif tpl['type'] == 'regen':
        user['energy_regen'] += tpl['effect']

    con.execute("""
        UPDATE users SET balance = ?, upgrades = ?, energy_max = ?, energy_regen = ?
        WHERE code = ?
    """, (user['balance'], json.dumps(user['upgrades']),
          user['energy_max'], user['energy_regen'], code))
    con.commit()

    new_ach = check_achievements(con, user)
    con.close()

    return jsonify({'user': user, 'passive': get_passive(user), 'new_achievements': new_ach})


@app.route('/api/skin', methods=['POST'])
def buy_skin():
    data = request.get_json() or {}
    code = data.get('code')
    sid = data.get('skin_id')
    if not code or sid not in SKINS:
        return jsonify({'error': 'bad request'}), 400

    con = db()
    row = con.execute("SELECT * FROM users WHERE code = ?", (code,)).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'not found'}), 404

    user = user_to_dict(row)
    if sid in user['owned_skins']:
        user['current_skin'] = sid
        con.execute("UPDATE users SET current_skin = ? WHERE code = ?", (sid, code))
        con.commit()
        con.close()
        return jsonify({'user': user, 'passive': get_passive(user)})

    price = SKINS[sid]['price']
    if user['balance'] < price:
        con.close()
        return jsonify({'error': 'no money', 'user': user})

    user['balance'] -= price
    user['owned_skins'].append(sid)
    user['current_skin'] = sid

    con.execute("""
        UPDATE users SET balance = ?, owned_skins = ?, current_skin = ?
        WHERE code = ?
    """, (user['balance'], json.dumps(user['owned_skins']), sid, code))
    con.commit()
    con.close()

    return jsonify({'user': user, 'passive': get_passive(user)})


@app.route('/api/daily', methods=['POST'])
def daily():
    data = request.get_json() or {}
    code = data.get('code')
    if not code:
        return jsonify({'error': 'no code'}), 400

    con = db()
    row = con.execute("SELECT * FROM users WHERE code = ?", (code,)).fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'not found'}), 404

    user = user_to_dict(row)
    now = int(time.time())
    if now - user['last_daily'] < 24 * 3600:
        con.close()
        return jsonify({'error': 'cooldown', 'user': user})

    amount = random.randint(5000, 50000)
    user['balance'] += amount

    con.execute("UPDATE users SET balance = ?, last_daily = ? WHERE code = ?",
                (user['balance'], now, code))
    con.commit()
    con.close()

    return jsonify({'user': user, 'amount': amount, 'passive': get_passive(user)})


@app.route('/api/leaderboard', methods=['GET'])
def leaderboard():
    con = db()
    rows = con.execute("""
        SELECT code, name, balance FROM users
        ORDER BY balance DESC LIMIT 20
    """).fetchall()
    con.close()
    return jsonify({'top': [dict(r) for r in rows]})


# ===== ЗАПУСК =====
init_db()

if __name__ == '__main__':
    print("\n🚽  Скибиди Кликер сервер запущен!")
    print("🌐  Открой в браузере: http://127.0.0.1:5000\n")
    app.run(host='0.0.0.0', port=5000, debug=True)
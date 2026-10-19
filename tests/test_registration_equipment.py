from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
import sqlite3
import pytest
from conftest import login
from bronylab.repository import Repository, SCHEMA
from bronylab.config import load_config
from bronylab.errors import LabError
from werkzeug.security import check_password_hash, generate_password_hash


def register(client, **changes):
    token = client.get('/api/session').json['csrf_token']
    payload = dict(name='Анна Смирнова', email='anna@g.nsu.ru', password='SecurePassword2026!', password_confirm='SecurePassword2026!')
    payload.update(changes)
    return client.post('/api/register', json=payload, headers={'X-CSRF-Token':token})


def admin_headers(app):
    config = app.extensions['lab_config']
    config['all_users'].append(dict(id='admin-01',name='Администратор',role='admin',password_hash=generate_password_hash('admin-password',method='pbkdf2:sha256:1000')))
    app.extensions['lab_repository'].initialize(config)
    client = app.test_client()
    return client, login(client, 'admin-01', 'admin-password')


ITEM = dict(name='Штатив Manfrotto Compact', location='Медиакласс, ауд. 312', category='Аксессуары', description='Лёгкий штатив для учебных съёмок')


def test_registration_login_booking_and_restart(app, client, booking):
    response = register(client, email='  ANNA@G.NSU.RU  ')
    assert response.status_code == 201
    user = response.json['user']
    assert user['role'] == 'student' and user['name'] == 'Анна Смирнова'
    assert 'password_hash' not in user
    headers = {'X-CSRF-Token':response.json['csrf_token']}
    assert client.get('/api/session').json['user']['id'] == user['id']
    assert client.post('/api/bookings',json={**booking,'user_id':user['id']},headers=headers).status_code == 201
    repo = app.extensions['lab_repository']
    row = repo.user(user['id'])
    assert row['email'] == 'anna@g.nsu.ru' and row['source'] == 'web'
    assert check_password_hash(row['password_hash'], 'SecurePassword2026!')
    repo.initialize(app.extensions['lab_config'])
    client.post('/api/logout',json={},headers=headers)
    login(client,'ANNA@g.nsu.ru','SecurePassword2026!')
    assert len(client.get('/api/bookings').json['bookings']) == 1


@pytest.mark.parametrize('email', ['a@gmail.com','a@nsu.ru','a@g.nsu.ru.evil.com','a@g.nsu.ru@evil.com','a@sub.g.nsu.ru','a@@g.nsu.ru','a..b@g.nsu.ru','.a@g.nsu.ru','a.@g.nsu.ru','а@g.nsu.ru','@g.nsu.ru','a b@g.nsu.ru'])
def test_registration_exact_domain(client, email):
    response = register(client,email=email)
    assert response.status_code == 400
    assert response.json['error']['code'] == 'EMAIL_DOMAIN'


@pytest.mark.parametrize('changes', [dict(name='   '),dict(name='a'*151),dict(password='short',password_confirm='short'),dict(password=' '*11,password_confirm=' '*11),dict(password='x'*129,password_confirm='x'*129),dict(password_confirm='different-password'),dict(role='admin'),dict(active=1),dict(name=['bad'])])
def test_bad_registration(client, changes):
    assert register(client,**changes).status_code == 400


def test_registration_csrf_and_no_promotion(client):
    assert client.post('/api/register',json={}).status_code == 403
    headers = login(client)
    response = register(client)
    assert response.status_code == 403
    assert client.get('/api/session').json['user']['role'] == 'student'


def test_email_duplicate_case_insensitive(app):
    assert register(app.test_client()).status_code == 201
    response = register(app.test_client(),email='ANNA@G.NSU.RU')
    assert response.status_code == 409 and response.json['error']['code'] == 'EMAIL_EXISTS'


def test_registration_rate_limit(client):
    for n in range(5):
        assert register(client,email='invalid@gmail.com').status_code == 400
    assert register(client).status_code == 429


def test_admin_creates_card_and_persistence(app):
    client, headers = admin_headers(app)
    response = client.post('/api/equipment',json=ITEM,headers=headers)
    assert response.status_code == 201
    item = response.json['equipment']
    assert item['name'] == ITEM['name'] and item['source'] == 'web'
    assert item['status'] == 'available'
    repo = app.extensions['lab_repository']
    repo.initialize(app.extensions['lab_config'])
    assert any(e['id']==item['id'] for e in repo.catalog())
    # Student can reserve a card created by the administrator.
    student = app.test_client()
    h = login(student)
    assert student.post('/api/bookings',json=dict(equipment_id=item['id'],date='2026-10-19',start_time='10:00',end_time='11:00'),headers=h).status_code == 201


@pytest.mark.parametrize('role', ['student','teacher'])
def test_non_admin_cannot_add(app,role):
    client = app.test_client()
    uid,password = ('student-01','test-password') if role=='student' else ('teacher-01','teacher-password')
    headers = login(client,uid,password)
    assert client.post('/api/equipment',json=ITEM,headers=headers).status_code == 403
    assert len(app.extensions['lab_repository'].catalog()) == 1


@pytest.mark.parametrize('change',[dict(name=' '),dict(name='x'*151),dict(location=''),dict(category=''),dict(category='x'*81),dict(description='x'*2001),dict(status='hidden'),dict(id='camera-01'),dict(location=['bad'])])
def test_admin_card_validation(app,change):
    client,headers = admin_headers(app)
    assert client.post('/api/equipment',json={**ITEM,**change},headers=headers).status_code == 400


def test_admin_csrf_and_no_auth(app):
    assert app.test_client().post('/api/equipment',json=ITEM).status_code == 403
    anonymous = app.test_client()
    token = anonymous.get('/api/session').json['csrf_token']
    assert anonymous.post('/api/equipment',json=ITEM,headers={'X-CSRF-Token':token}).status_code == 401
    client,headers = admin_headers(app)
    assert client.post('/api/equipment',json=ITEM).status_code == 403


def test_admin_removed_mid_session(app):
    client,headers = admin_headers(app)
    config = app.extensions['lab_config']
    config['all_users'] = [u for u in config['all_users'] if u['id']!='admin-01']
    app.extensions['lab_repository'].initialize(config)
    assert client.post('/api/equipment',json=ITEM,headers=headers).status_code == 401


def test_limit_serializes_concurrent_adds(app):
    client,headers = admin_headers(app)
    repo = app.extensions['lab_repository']
    for n in range(27):
        repo.add_equipment('admin-01',ITEM)
    gate=Barrier(4)
    def add(n):
        target=Repository(repo.path)
        gate.wait()
        try:
            target.add_equipment('admin-01',ITEM)
            return 'created'
        except LabError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(add,range(4)))
    assert results.count('created')==1 and results.count('EQUIPMENT_LIMIT')==3
    with repo.connection() as con:
        assert con.execute('SELECT count(*) FROM equipment').fetchone()[0]==30


def test_old_database_migration_preserves_existing(config_path, booking):
    config=load_config(config_path)
    Path(config['database']).parent.mkdir()
    con=sqlite3.connect(config['database'])
    con.executescript(SCHEMA)
    con.execute("INSERT INTO equipment VALUES('camera-01','Камера','312','','Камеры','available')")
    old_hash=generate_password_hash('old-password',method='pbkdf2:sha256:1000')
    con.execute("INSERT INTO users VALUES('student-01','Студент','student',?,1)",(old_hash,))
    con.execute("INSERT INTO bookings(id,equipment_id,user_id,date,start_time,end_time) VALUES('old-booking','camera-01','student-01','2026-10-06','10:00','12:00')")
    con.commit();con.close()
    repo=Repository(config['database'])
    repo.initialize(config);repo.initialize(config)
    assert repo.user('student-01')['password_hash']==old_hash
    assert repo.mine('student-01')[0]['id']=='old-booking'
    assert repo.user('student-01')['source']=='config'
    assert repo.user('student-01')['email'] is None


def test_dry_snapshot_preserves_web_entities(app):
    client,headers=admin_headers(app)
    item=client.post('/api/equipment',json=ITEM,headers=headers).json['equipment']
    newcomer=register(app.test_client()).json['user']
    repo=app.extensions['lab_repository']
    original=Path(repo.path).read_bytes()
    clone=repo.snapshot(app.extensions['lab_config'])
    try:
        assert clone.user(newcomer['id'])['role']=='student'
        assert any(e['id']==item['id'] for e in clone.catalog())
    finally:
        clone.memory.close()
    assert Path(repo.path).read_bytes()==original


def test_concurrent_registration_unique_email(app):
    repo=app.extensions['lab_repository']
    gate=Barrier(4)
    password_hash=generate_password_hash('new-password',method='pbkdf2:sha256:1000')
    def attempt(n):
        target=Repository(repo.path)
        gate.wait()
        try:
            target.register_user('Студент','concurrent@g.nsu.ru',password_hash)
            return 'created'
        except LabError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(attempt,range(4)))
    assert results.count('created')==1 and results.count('EMAIL_EXISTS')==3


def test_public_help_and_images(client):
    import re
    page=client.get('/help')
    assert page.status_code==200
    html=page.data.decode('utf-8')
    assert 'ЧАСТЫЕ ВОПРОСЫ' in html
    assert 'Письмо или код подтверждения не отправляется' in html
    images=re.findall(r'src="([^"]+\.png)"',html)
    assert len(images)==9
    for path in images:
        image=client.get(path)
        assert image.status_code==200 and image.mimetype=='image/png'

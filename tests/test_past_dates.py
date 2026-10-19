from datetime import datetime, timezone
from pathlib import Path
import pytest
import yaml
from conftest import login
from bronylab.cli import main
from bronylab.errors import LabError
from bronylab.service import department_today


@pytest.mark.parametrize('date,allowed', [('2026-10-05',False),('2026-10-06',True),('2026-10-07',True)])
def test_date_boundary(lab, booking, date, allowed):
    if allowed:
        assert lab[2].book({**booking,'date':date})['action']=='BOOK'
    else:
        with pytest.raises(LabError,match='PAST_DATE'):
            lab[2].book({**booking,'date':date})
        assert lab[1].mine('student-01')==[]


@pytest.mark.parametrize('zone,utc_time,expected',[
    ('Asia/Novosibirsk','2026-10-05T18:00:00+00:00','2026-10-06'),
    ('Pacific/Honolulu','2026-10-06T05:00:00+00:00','2026-10-05'),
])
def test_department_timezone(lab,booking,lab_clock,zone,utc_time,expected):
    lab_clock['now']=datetime.fromisoformat(utc_time)
    lab[0]['timezone']=zone
    lab[0]['work_hours']={'start':'00:00','end':'23:59'}
    assert department_today(lab[0])==expected
    assert lab[2].book({**booking,'date':expected,'start_time':'20:00','end_time':'21:00'})['action']=='BOOK'


def test_midnight_rechecks_server_date(client,booking,lab_clock):
    headers=login(client)
    assert client.get('/api/session').json['today']=='2026-10-06'
    lab_clock['now']=datetime(2026,10,6,18,tzinfo=timezone.utc)
    assert client.get('/api/session').json['today']=='2026-10-07'
    result=client.post('/api/bookings',json=booking,headers=headers)
    assert result.status_code==400 and result.json['error']['code']=='PAST_DATE'
    assert client.get('/api/bookings').json['bookings']==[]


def test_history_still_visible_and_retry_is_idempotent(client,booking,lab_clock):
    headers=login(client)
    assert client.post('/api/bookings',json=booking,headers=headers).status_code==201
    lab_clock['now']=datetime(2026,10,7,5,tzinfo=timezone.utc)
    assert len(client.get('/api/bookings').json['bookings'])==1
    assert len(client.get('/api/equipment/camera-01/bookings?date=2026-10-06').json['bookings'])==1
    assert client.post('/api/bookings',json=booking,headers=headers).status_code==200
    assert len(client.get('/api/bookings').json['bookings'])==1


@pytest.mark.parametrize('dry_run',[False,True])
def test_cli_rejects_new_past_booking(config_path,booking,capsys,dry_run):
    config=yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config['bookings']=[{**booking,'date':'2026-10-05'}]
    config_path.write_text(yaml.safe_dump(config),encoding='utf-8')
    args=['run','--config',str(config_path)]+(['--dry-run'] if dry_run else [])
    assert main(args)==1
    result=capsys.readouterr()
    assert 'ERROR PAST_DATE' in result.err and 'bookings=0' in result.out
    if dry_run:
        assert not (config_path.parent/'data/bronylab.sqlite3').exists()

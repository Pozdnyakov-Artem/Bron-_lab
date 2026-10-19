from datetime import datetime, timezone
import pytest
import yaml
from conftest import login
from bronylab.cli import main
from bronylab.errors import LabError
from bronylab.service import execute_batch


@pytest.mark.parametrize('start,end', [('09:00','10:00'),('11:30','13:00')])
def test_elapsed_or_already_started_interval_rejected(lab,booking,lab_clock,start,end):
    lab_clock['now']=datetime(2026,10,6,5,tzinfo=timezone.utc)  # 12:00 Novosibirsk
    with pytest.raises(LabError,match='PAST_TIME'):
        lab[2].book({**booking,'start_time':start,'end_time':end})
    assert lab[1].mine('student-01')==[]


@pytest.mark.parametrize('seconds,start,allowed',[(0,'12:00',True),(1,'12:00',False),(30,'12:01',True),(59,'12:01',True)])
def test_minute_boundary(lab,booking,lab_clock,seconds,start,allowed):
    lab_clock['now']=datetime(2026,10,6,5,0,seconds,tzinfo=timezone.utc)
    item={**booking,'start_time':start,'end_time':'13:00'}
    if allowed:
        assert lab[2].book(item)['action']=='BOOK'
    else:
        with pytest.raises(LabError,match='PAST_TIME'):
            lab[2].book(item)


def test_future_day_morning_remains_available(lab,booking,lab_clock):
    lab_clock['now']=datetime(2026,10,6,5,tzinfo=timezone.utc)
    assert lab[2].book({**booking,'date':'2026-10-07','start_time':'09:00','end_time':'10:00'})['action']=='BOOK'


def test_server_clock_and_stale_form(client,booking,lab_clock):
    headers=login(client)
    # Form opened at 08:00, submitted at 12:00:30 local time.
    assert client.get('/api/session').json['now'].startswith('2026-10-06T08:00:00+07:00')
    lab_clock['now']=datetime(2026,10,6,5,0,30,tzinfo=timezone.utc)
    settings=client.get('/api/session').json
    assert settings['now'].startswith('2026-10-06T12:00:30+07:00')
    assert settings['earliest_date']=='2026-10-06' and settings['earliest_time']=='12:01'
    result=client.post('/api/bookings',json=booking,headers=headers)
    assert result.status_code==400 and result.json['error']['code']=='PAST_TIME'
    assert client.get('/api/bookings').json['bookings']==[]


def test_day_end_rounding(client,lab_clock):
    lab_clock['now']=datetime(2026,10,6,16,59,59,tzinfo=timezone.utc)
    settings=client.get('/api/session').json
    assert settings['today']=='2026-10-06'
    assert settings['earliest_date']=='2026-10-07' and settings['earliest_time']=='00:00'


@pytest.mark.parametrize('dry_run',[False,True])
def test_cli_rejects_elapsed_time(config_path,booking,lab_clock,capsys,dry_run):
    lab_clock['now']=datetime(2026,10,6,5,tzinfo=timezone.utc)
    config=yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config['bookings']=[booking]
    config_path.write_text(yaml.safe_dump(config),encoding='utf-8')
    args=['run','--config',str(config_path)]+(['--dry-run'] if dry_run else [])
    assert main(args)==1
    assert 'PAST_TIME' in capsys.readouterr().err

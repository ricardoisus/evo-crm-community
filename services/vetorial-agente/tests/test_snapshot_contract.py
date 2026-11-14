import pytest
from vetorial_agent.configuration import validate, InvalidConfiguration
from vetorial_agent.import_a14 import candidate

def payload():
    return candidate(deployment_id='synthetic', bot_id='bot', inbox_id='inbox', pipeline_id=None,
                     allowlist=['+15550000002'], internal_numbers=['+15550000001'], project_id='proj_synthetic',
                     event_type='https://api.calendly.com/event_types/00000000-0000-4000-8000-000000000004',
                     calendar_id='synthetic@example.invalid')['payload']

@pytest.mark.parametrize('section', ['binding','activation','behavior','limits','integrations','provider','runtime'])
def test_nested_unknown_fields_are_rejected(section):
    data=payload()
    data[section]['api_key']='never-accepted'
    with pytest.raises(InvalidConfiguration):
        validate(data)

@pytest.mark.parametrize('path,value', [
    (['limits','messages_per_hour'],21), (['limits','max_tool_rounds'],0),
    (['runtime','history_messages'],1001), (['runtime','media_items'],-1),
    (['provider','service_tier'],'unexpected'), (['provider','project_id'],42),
    (['binding','inbox_ids'],['']), (['integrations','calendar_id'],None),
    (['integrations','booking_minutes'],30),
    (['roles','atendimento','output_schema'],[]),
    (['roles','atendimento','actions'],{'api_key':'no'}),
    (['roles','analista','prompt_version'],None),
    (['roles','analista','output_schema'],{'$ref':'https://private.invalid/schema'}),
    (['roles','analista','output_schema'],{'properties':{'text':{'api_key':'no'}}}),
])
def test_invalid_nested_contract(path,value):
    data=payload()
    target=data
    for key in path[:-1]:
        target=target[key]
    target[path[-1]]=value
    with pytest.raises(InvalidConfiguration):
        validate(data)

def test_original_a14_contract_remains_valid():
    assert validate(payload())['integrations']['booking_minutes']==45

def test_office_hours_and_pilot_constraints():
    data=payload()
    data['activation']['allowlist']=[]
    with pytest.raises(InvalidConfiguration):
        validate(data)
    data=payload()
    data['behavior']['office_hours']={'enabled':True,'windows':[
        {'weekday':1,'start':'09:00','end':'12:00'},{'weekday':1,'start':'11:00','end':'13:00'}]}
    with pytest.raises(InvalidConfiguration):
        validate(data)

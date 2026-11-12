"""Offline repricing of returned usage. Does not read credentials or call APIs."""
import argparse
import json
from pathlib import Path
import re
from .budget import RATES


def price(model, usage, cold=False):
    rates=RATES[model]
    details=usage.get('input_tokens_details',{})
    cached=0 if cold else details.get('cached_tokens',0)
    written=usage['input_tokens'] if cold else details.get('cache_write_tokens',0)
    return (max(0,usage['input_tokens']-cached-written)*rates[0] + usage['output_tokens']*rates[1]
            + cached*rates[2]+written*rates[3])/1e6


def main():
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);args=p.parse_args();out=args.output
    meter=json.loads((out/'meter.json').read_text())
    calls=[c for c in meter['calls'] if c['provider']=='openai']
    total=sum(price(c['model'],c['usage']) for c in calls)
    summary={}
    for model in RATES:
        rows=[json.loads(p.read_text()) for p in out.glob(model+'-*.json') if re.search(r'-[0-9]{2}\.json$',p.name)]
        complete=[r for r in rows if r.get('usage') and not any(t.get('error') for t in r.get('turns',[]))]
        costs=[sum(price(u.get('_model') or model,u) for u in r['usage'] if 'input_tokens' in u) for r in complete]
        cold=[sum(price(u.get('_model') or model,u,cold=True) for u in r['usage'] if 'input_tokens' in u) for r in complete]
        summary[model]={
            'completed_synthetic_conversations':len(complete),
            'per_scenario_usd':dict(zip([r['scenario'] for r in complete],costs)),
            'mean_conversation_usd':sum(costs)/len(costs) if costs else None,
            '100_conversations_usd':sum(costs)/len(costs)*100 if costs else None,
            '100_conversations_cold_cache_usd':sum(cold)/len(cold)*100 if cold else None,
        }
    result={'openai_returned_usage_usd':total,'openai_guard_accumulated_usd':meter['openai_usd'],
            'groq_usd':meter['groq_usd'],'pending_reservation':meter.get('pending'),
            'rates_source':'https://developers.openai.com/api/docs/pricing',
            'cache_ttl_source':'https://developers.openai.com/api/docs/guides/prompt-caching',
            'rates_standard_per_million':RATES,'cache_ttl':'30m (default; no request used 12h)',
            'models':summary,
            'limits':'Synthetic short conversations; no production usage panel reconciliation. A pending transport reservation may still be billed; do not treat unknown cost as zero.'}
    (out/'cost-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'returned_usage_usd':total,'guard_usd':meter['openai_usd'],'groq_usd':meter['groq_usd']}))


if __name__=='__main__':main()

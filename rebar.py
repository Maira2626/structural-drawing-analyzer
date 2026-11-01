"""Read numeric reinforcement schedules using their actual PDF column positions.

Supports rectangular 3xN and 6xN tables with heading strength columns.
Other structures are skipped and explicitly flagged. No invented cells/strengths.
"""
import re
from collections import defaultdict

TITLES=(('Lap splice','LAP SPLICE LENGTH'),('Straight development','STRAIGHT BAR DEVELOPMENT LENGTH'),('Hooked development','HOOKED TENSION DEVELOPMENT'))
BAR=re.compile(r'#(?:[3-9]|10|11|14)\b',re.I)
PSI=re.compile(r'^(\d{1,2}[,]?\d{3})$')

def extract_rebar_dynamic(page, file, sheet):
    words=page.get_text('words', sort=False)
    blocks=page.get_text('blocks',sort=False)
    results=[];issues=[]
    for title,phrase in TITLES:
        headings=[b for b in blocks if b[4].strip().upper().startswith(phrase)]
        if not headings:continue
        head=min(headings,key=lambda b:b[1]);hx,hy=head[0:2]
        is_hook=title=='Hooked development'
        num_per_group=2 if is_hook else 3
        # Read the concrete strengths printed in the table header. No fixed 6k/8k/10k assumption.
        header_words=[w for w in words if hy+7<w[1]<hy+(55 if is_hook else 65)
                      and hx-220<w[0]<hx+(620 if is_hook else 420)]
        hdr=[]
        for w in sorted(header_words,key=lambda x:x[0]):
            v=w[4].strip('.,')
            if re.fullmatch(r'(?:\d{1,2},\d{3}|\d{4,5})',v):
                psi=int(v.replace(',',''))
                if 1000<=psi<=30000:
                    after=[z for z in header_words if 0<=z[0]-w[2]<18 and abs(z[1]-w[1])<5 and z[4].upper().startswith('PSI')]
                    if after:hdr.append((w[0],psi))
        if is_hook:
            # Hooked tension development & straight hook extension share six columns.
            if len(hdr)!=6 or [a[1] for a in hdr[:3]] != [a[1] for a in hdr[3:]]:
                issues.append({'file':file,'sheet':sheet,'page':page.number+1,'warning':'Hooked development: unsupported header configuration; verify against drawing.'})
                continue
            strengths=[val for _,val in hdr[:3]]
        else:
            if len(hdr)!=3:
                issues.append({'file':file,'sheet':sheet,'page':page.number+1,'warning':f'{title}: unsupported strength-column heading; no table values assumed.'})
                continue
            strengths=[val for _,val in hdr]
        starts=[w for w in words if BAR.fullmatch(w[4]) and hx-220<w[0]<hx+75 and hy+(42 if is_hook else 80)<w[1]<hy+330]
        if not starts:
            issues.append({'file':file,'sheet':sheet,'page':page.number+1,'warning':f'{title}: no #bar-size rows found.'})
            continue
        groups=defaultdict(list)
        for w in starts:groups[round(w[0]/12)*12].append(w)
        starts=sorted(max(groups.values(),key=len),key=lambda w:w[1])
        bar_x=sum(w[0] for w in starts)/len(starts)
        min_num=6 if is_hook else 9
        right=max(x for x,v in hdr)+110
        for lbl in starts:
            numerics=[w for w in words if BAR.fullmatch(w[4]) is None and re.fullmatch(r'\d{1,3}',w[4])
                    and bar_x+22<w[0]<right and abs(w[1]-lbl[1])<4.5]
            numerics.sort(key=lambda w:w[0])
            # Numeric tokens map left-to-right to the schedule cells, preserving column groups.
            if len(numerics)!=min_num:
                issues.append({'file':file,'sheet':sheet,'page':page.number+1,
                    'warning':f"{title} {lbl[4]}: expected {min_num} cells; found {len(numerics)}; row skipped."})
                continue
            vals=[int(w[4]) for w in numerics]
            for k,psi in enumerate(strengths):
                if is_hook:
                    typ, ext=vals[k],vals[k+3];top=comp=None
                else:
                    typ, top, comp=vals[k*3:(k+1)*3];ext=None
                results.append({'file':file,'sheet':sheet,'page':page.number+1,'schedule':title,
                    'bar_size':lbl[4],'concrete_psi':psi,'tension_typ_in':typ,
                    'tension_top_in':top,'compression_in':comp,'hook_extension_in':ext})
    return results,issues

const pptx = require('pptxgenjs');
const p = new pptx();
p.layout = 'LAYOUT_WIDE';           // 13.3 x 7.5
p.author = 'Concordance';
p.title  = 'Concordance — Novo Nordisk GBS Hackathon 2026, PS 5';

// Palette taken from the product itself, so the deck and the tool read as one thing.
const INK='14202B', PAPER='F1F3F5', WHITE='FFFFFF', ACCENT='2C5985',
      SOFT='DCE8F2', MUTED='5C6B74', FAINT='8896A0', RULE='D3DBE0',
      OK='2E7D5B', OKBG='E4F3EC', WARN='9A6B1E', WARNBG='FBF0DD',
      CRIT='A33B3B', CRITBG='FBE7E7';
const SERIF='Cambria', SANS='Calibri', MONO='Courier New';

const W=13.3, H=7.5, M=0.7;

function dark(s){ s.background={color:INK}; }
function light(s){ s.background={color:PAPER}; }

// The motif: a real fingerprint, in monospace, sitting quietly on every slide.
function mark(s, hex, onDark){
  s.addText(hex, {x:W-3.3, y:H-0.52, w:2.6, h:0.28, align:'right',
    fontFace:MONO, fontSize:9, color:onDark?'3A4A57':FAINT});
}
function title(s, text, sub){
  s.addText(text, {x:M, y:0.5, w:W-2*M, h:0.72, fontFace:SERIF, fontSize:34, bold:true, color:INK, margin:0});
  if(sub) s.addText(sub, {x:M, y:1.22, w:W-2*M, h:0.4, fontFace:SANS, fontSize:14, color:MUTED, margin:0});
}
function stat(s,x,y,w,value,label,tone){
  const c = tone||INK;
  s.addShape(p.ShapeType.roundRect,{x,y,w,h:1.25,fill:{color:WHITE},line:{color:RULE,width:0.75},rectRadius:0.04});
  s.addText(String(value),{x:x+0.02,y:y+0.12,w:w-0.04,h:0.62,align:'center',fontFace:SERIF,fontSize:34,bold:true,color:c,margin:0});
  s.addText(label,{x:x+0.06,y:y+0.76,w:w-0.12,h:0.4,align:'center',fontFace:SANS,fontSize:10.5,color:MUTED,margin:0});
}
function card(s,x,y,w,h,head,body,tone){
  s.addShape(p.ShapeType.roundRect,{x,y,w,h,fill:{color:WHITE},line:{color:RULE,width:0.75},rectRadius:0.04});
  s.addShape(p.ShapeType.ellipse,{x:x+0.28,y:y+0.28,w:0.34,h:0.34,fill:{color:tone||SOFT}});
  s.addText(head,{x:x+0.75,y:y+0.24,w:w-1.0,h:0.42,fontFace:SANS,fontSize:14,bold:true,color:INK,margin:0});
  s.addText(body,{x:x+0.3,y:y+0.78,w:w-0.6,h:h-1.05,fontFace:SANS,fontSize:12,color:MUTED,margin:0,lineSpacingMultiple:1.15});
}

/* 1 — title */
let s=p.addSlide(); dark(s);
s.addText('Concordance',{x:M,y:2.1,w:W-2*M,h:1.0,fontFace:SERIF,fontSize:54,bold:true,color:WHITE,margin:0});
s.addText('A semantic contract between a BRD and the Power BI model it describes',
  {x:M,y:3.1,w:9.6,h:0.9,fontFace:SANS,fontSize:19,color:'C2CDD2',margin:0});
s.addText([
  {text:'Novo Nordisk GBS Hackathon 2026',options:{bold:true,color:WHITE}},
  {text:'   ·   Problem Statement 5',options:{color:'93A2A9'}},
],{x:M,y:4.5,w:9.6,h:0.4,fontFace:SANS,fontSize:13,margin:0});
s.addText('Requirements are not written from the model. They are derived from it, and every sentence carries the object it came from.',
  {x:M,y:5.15,w:9.2,h:0.8,fontFace:SANS,fontSize:12.5,italic:true,color:'93A2A9',margin:0});
mark(s,'2c7cbf0aa669',true);
s.addNotes('Concordance turns a Power BI semantic model into a BRD and FRD where every statement is traceable to the object it was derived from, and stays checkable as the model changes.');

/* 2 — the problem */
s=p.addSlide(); light(s);
title(s,'The document and the model drift apart','and nothing in either one says when it happened');
card(s,M,1.9,3.85,2.0,'Written by hand','A BRD is typed from interviews and screenshots. It is correct on the day it is signed and never verified again.',WARNBG);
card(s,M+4.05,1.9,3.85,2.0,'Changed without notice','Someone edits one filter in one measure. The number moves. The document still describes the old logic.',CRITBG);
card(s,M+8.1,1.9,3.85,2.0,'Found in a meeting','Two reports disagree. The reconciliation is manual, and nobody can say which definition is authoritative.',CRITBG);
s.addShape(p.ShapeType.roundRect,{x:M,y:4.25,w:W-2*M,h:1.55,fill:{color:WHITE},line:{color:RULE,width:0.75},rectRadius:0.03});
s.addText('The gap is not documentation effort. It is that no link exists between a sentence and the logic it describes — so nothing can tell you the sentence stopped being true.',
  {x:M+0.35,y:4.45,w:W-2*M-0.7,h:0.7,fontFace:SANS,fontSize:15,color:INK,margin:0});
s.addText('In a regulated environment that gap is an audit finding, not an inconvenience.',
  {x:M+0.35,y:5.15,w:W-2*M-0.7,h:0.45,fontFace:SANS,fontSize:13,italic:true,color:MUTED,margin:0});
mark(s,'76fa30df61e0');
s.addNotes('The problem is not that documentation is slow to write. It is that a document and a model have no mechanical link, so drift is invisible.');

/* 3 — what it does */
s=p.addSlide(); light(s);
title(s,'What Concordance does','one extraction, five things that stay true afterwards');
const items=[
  ['Derives the BRD and FRD','Every statement comes from an object in the model, and shows which one.'],
  ['Fingerprints the logic','Canonicalised DAX, hashed. Reformatting is silent; a changed filter is not.'],
  ['Detects drift between versions','Names the requirements a change puts in question, and the ones it provably does not.'],
  ['Reconciles across platforms','Compares what a DAX measure and a warehouse view each read, and reports the difference.'],
  ['Answers questions about the model','Fifteen read-only tools over the graph. Every answer is a real tool call, never recall.'],
];
items.forEach(([h,b],i)=>{
  const y=1.95+i*0.99;
  s.addShape(p.ShapeType.ellipse,{x:M,y:y+0.03,w:0.38,h:0.38,fill:{color:ACCENT}});
  s.addText(String(i+1),{x:M,y:y+0.03,w:0.38,h:0.38,align:'center',valign:'middle',fontFace:SANS,fontSize:13,bold:true,color:WHITE,margin:0});
  s.addText(h,{x:M+0.58,y:y-0.02,w:5.4,h:0.34,fontFace:SANS,fontSize:14,bold:true,color:INK,margin:0});
  s.addText(b,{x:M+0.58,y:y+0.3,w:5.7,h:0.56,fontFace:SANS,fontSize:11.5,color:MUTED,margin:0});
});
s.addShape(p.ShapeType.roundRect,{x:7.6,y:1.9,w:5.0,h:4.9,fill:{color:INK},line:{color:INK},rectRadius:0.03});
s.addText('The claim',{x:7.95,y:2.15,w:4.3,h:0.4,fontFace:SANS,fontSize:11,bold:true,color:'8896A0',charSpacing:1.5,margin:0});
s.addText('A requirement is bound to a fingerprint.',{x:7.95,y:2.6,w:4.3,h:0.9,fontFace:SERIF,fontSize:21,bold:true,color:WHITE,margin:0});
s.addText('Change the logic and the binding breaks — automatically, with no one remembering to check. That is the whole design.',
  {x:7.95,y:3.6,w:4.3,h:1.1,fontFace:SANS,fontSize:12.5,color:'C2CDD2',margin:0,lineSpacingMultiple:1.2});
s.addText('REQ-B-58c30b',{x:7.95,y:4.85,w:4.3,h:0.3,fontFace:MONO,fontSize:11,color:'6C7A80',margin:0});
s.addText('bound to  20128b29d1e1',{x:7.95,y:5.15,w:4.3,h:0.3,fontFace:MONO,fontSize:11,color:'6C7A80',margin:0});
s.addText('now       ee32bfb1016c   STALE',{x:7.95,y:5.45,w:4.3,h:0.3,fontFace:MONO,fontSize:11,color:'E0736E',margin:0});
mark(s,'20128b29d1e1');
s.addNotes('Five capabilities, one mechanism underneath: the fingerprint binding. The chatbot is grounded in the same graph, so it cannot contradict the documents.');

/* 4 — how it works */
s=p.addSlide(); light(s);
title(s,'How it works','one pipeline, seven real models, two Power BI formats');
const steps=[['.pbix / TMDL','Two adapters, one model shape'],
             ['Canonical\nSemantic Graph','Tables, measures, joins,\ncolumns, sources'],
             ['SHA-256\nfingerprints','Over canonicalised DAX,\nnot raw text'],
             ['BRD / FRD\n+ evidence','Every statement carries\nits objects']];
steps.forEach(([h,b],i)=>{
  const x=M+i*3.15;
  s.addShape(p.ShapeType.roundRect,{x,y:2.2,w:2.75,h:1.75,fill:{color:WHITE},line:{color:RULE,width:0.75},rectRadius:0.04});
  s.addText(h,{x:x+0.15,y:2.38,w:2.45,h:0.7,align:'center',fontFace:SANS,fontSize:13.5,bold:true,color:INK,margin:0});
  s.addText(b,{x:x+0.15,y:3.06,w:2.45,h:0.75,align:'center',fontFace:SANS,fontSize:10.5,color:MUTED,margin:0});
  if(i<3) s.addText('→',{x:x+2.78,y:2.85,w:0.35,h:0.4,align:'center',fontFace:SANS,fontSize:20,color:ACCENT,margin:0});
});
s.addText('The graph is the only source of truth downstream. The chatbot, the documents, drift and reconciliation all read it — none of them re-parse the model, so none of them can disagree with each other.',
  {x:M,y:4.3,w:W-2*M,h:0.7,fontFace:SANS,fontSize:13,color:INK,margin:0});
stat(s,M,5.15,2.9,'14,572','lines of Python');
stat(s,M+3.1,5.15,2.9,'5,901','lines of TypeScript');
stat(s,M+6.2,5.15,2.9,'797','automated tests',OK);
stat(s,M+9.3,5.15,2.6,'0','regex in the DAX lexer',ACCENT);
mark(s,'8c102048e494');
s.addNotes('DAX is lexed, not pattern-matched: a comment marker inside a string literal defeats regex, and the fingerprint scheme depends on getting that right.');

/* 5 — the proof */
s=p.addSlide(); dark(s);
s.addText('The fingerprint is the proof',{x:M,y:0.55,w:W-2*M,h:0.7,fontFace:SERIF,fontSize:34,bold:true,color:WHITE,margin:0});
s.addText('Run against QC Metrics[OOS Rate] in the real QualityControl model — output copied verbatim',
  {x:M,y:1.25,w:W-2*M,h:0.4,fontFace:SANS,fontSize:13,color:'93A2A9',margin:0});
s.addShape(p.ShapeType.roundRect,{x:M,y:1.95,w:7.3,h:3.5,fill:{color:'171E22'},line:{color:'2A343A',width:0.75},rectRadius:0.03});
[['original      2c7cbf0aa669','C2CDD2'],
 ['reformatted   2c7cbf0aa669   same — no drift','7FD1A8'],
 ['altered       76fa30df61e0   DRIFT DETECTED','E0736E'],
 ['',''],
 ['mutation applied:','8896A0'],
 ['  DIVIDE([OOS Results], [Tests Performed], 1)','C2CDD2']].forEach(([t,c],i)=>{
  if(!t) return;
  s.addText(t,{x:M+0.35,y:2.25+i*0.46,w:6.6,h:0.4,fontFace:MONO,fontSize:12.5,color:c,margin:0});
});
s.addText('Whitespace, casing and comments change nothing.',{x:8.6,y:2.15,w:4.0,h:0.5,fontFace:SANS,fontSize:14,bold:true,color:WHITE,margin:0});
s.addText('So a formatter run does not raise a false alarm, and nobody learns to ignore the alarms.',
  {x:8.6,y:2.65,w:4.0,h:0.9,fontFace:SANS,fontSize:12,color:'93A2A9',margin:0,lineSpacingMultiple:1.15});
s.addText('One character of meaning changes everything.',{x:8.6,y:3.75,w:4.0,h:0.5,fontFace:SANS,fontSize:14,bold:true,color:WHITE,margin:0});
s.addText('The zero-divide fallback became a one. Different number, different hash, every bound requirement flagged.',
  {x:8.6,y:4.25,w:4.0,h:1.0,fontFace:SANS,fontSize:12,color:'93A2A9',margin:0,lineSpacingMultiple:1.15});
s.addText('This is not a heuristic. Identical hash means identical logic, and that is what makes rename detection provable rather than guessed.',
  {x:M,y:5.75,w:W-2*M,h:0.7,fontFace:SANS,fontSize:13,italic:true,color:'C2CDD2',margin:0});
mark(s,'2c7cbf0aa669',true);
s.addNotes('Reformatting is silent, a changed filter is not. Everything else is built on this property.');

/* 6 — drift */
s=p.addSlide(); light(s);
title(s,'Drift, on two real model versions','ClinicalTrialSafety v1 → v2, extracted live — every figure below is from that run');
stat(s,M,1.95,2.35,'1','added',OK);
stat(s,M+2.5,1.95,2.35,'1','removed',CRIT);
stat(s,M+5.0,1.95,2.35,'4','changed',WARN);
stat(s,M+7.5,1.95,2.35,'103','unchanged');
stat(s,M+10.0,1.95,2.0,'8','requirements now in question',CRIT);
s.addShape(p.ShapeType.roundRect,{x:M,y:3.5,w:5.9,h:2.7,fill:{color:WHITE},line:{color:RULE,width:0.75},rectRadius:0.04});
s.addText('What a diff cannot tell you',{x:M+0.3,y:3.7,w:5.3,h:0.4,fontFace:SANS,fontSize:15,bold:true,color:INK,margin:0});
s.addText('Listing changed objects is what any diff does. Naming the eight requirements whose evidence sits on those objects is what turns a diff into "this document may no longer describe this model".',
  {x:M+0.3,y:4.15,w:5.3,h:1.4,fontFace:SANS,fontSize:12.5,color:MUTED,margin:0,lineSpacingMultiple:1.2});
s.addText('Requirement ids come from object identity, never content — so the id survives the change and the report can point at it.',
  {x:M+0.3,y:5.5,w:5.3,h:0.6,fontFace:SANS,fontSize:11,italic:true,color:FAINT,margin:0});
s.addShape(p.ShapeType.roundRect,{x:M+6.2,y:3.5,w:5.7,h:2.7,fill:{color:OKBG},line:{color:RULE,width:0.75},rectRadius:0.04});
s.addText('A rename is proven, not guessed',{x:M+6.5,y:3.7,w:5.1,h:0.4,fontFace:SANS,fontSize:15,bold:true,color:INK,margin:0});
s.addText('Same fingerprint under a new name means the logic is provably untouched. Those requirements need a reference update, not re-validation.',
  {x:M+6.5,y:4.15,w:5.1,h:1.1,fontFace:SANS,fontSize:12.5,color:MUTED,margin:0,lineSpacingMultiple:1.2});
s.addText('Splitting those two apart is the difference between a reviewer re-checking eight statements and re-checking a hundred.',
  {x:M+6.5,y:5.3,w:5.1,h:0.8,fontFace:SANS,fontSize:11.5,italic:true,color:OK,margin:0});
mark(s,'0542a4dda013');
s.addNotes('Real numbers from the two shipped fixture models, not illustrative.');

/* 7 — reconcile */
s=p.addSlide(); light(s);
title(s,'The same KPI, defined twice','QualityControl in Power BI against the same metrics in the warehouse — run live against DuckDB');
stat(s,M,1.9,2.9,'6','defined on both');
stat(s,M+3.1,1.9,2.9,'1','divergent',CRIT);
stat(s,M+6.2,1.9,2.9,'1','needs review',WARN);
stat(s,M+9.3,1.9,2.6,'4','consistent',OK);
s.addShape(p.ShapeType.roundRect,{x:M,y:3.4,w:W-2*M,h:1.5,fill:{color:CRITBG},line:{color:RULE,width:0.75},rectRadius:0.04});
s.addText('OOS Rate — divergent',{x:M+0.3,y:3.58,w:5.0,h:0.35,fontFace:SANS,fontSize:14,bold:true,color:CRIT,margin:0});
s.addText('Power BI reads testresult;  the warehouse reads batch, testresult',
  {x:M+0.3,y:3.98,w:11.0,h:0.35,fontFace:MONO,fontSize:12,color:INK,margin:0});
s.addText('The two divide by different denominators. They will report different numbers, and no fingerprint could have found it — DAX and SQL never hash alike, so what is compared is what each definition structurally reads.',
  {x:M+0.3,y:4.34,w:11.0,h:0.5,fontFace:SANS,fontSize:12,color:MUTED,margin:0});
s.addShape(p.ShapeType.roundRect,{x:M,y:5.15,w:W-2*M,h:1.0,fill:{color:WHITE},line:{color:RULE,width:0.75},rectRadius:0.04});
s.addText('Three verdicts, not pass/fail.',{x:M+0.3,y:5.28,w:11.0,h:0.3,fontFace:SANS,fontSize:13.5,bold:true,color:INK,margin:0});
s.addText('Deciding whether two arbitrary expressions in two languages compute the same number is undecidable in general. "Needs review" is the honest answer for a difference that may or may not change the number.',
  {x:M+0.3,y:5.6,w:11.0,h:0.45,fontFace:SANS,fontSize:11.5,color:MUTED,margin:0,lineSpacingMultiple:1.1});

/* The judged deliverable: the same code path reaches four enterprise platforms. */
s.addText('Same extraction path, four more platforms:',
  {x:M,y:6.32,w:3.5,h:0.3,fontFace:SANS,fontSize:11.5,bold:true,color:INK,margin:0});
['Snowflake','Databricks','Redshift','Athena'].forEach((n,i)=>{
  s.addShape(p.ShapeType.roundRect,{x:M+3.65+i*1.62,y:6.3,w:1.5,h:0.33,fill:{color:SOFT},line:{color:RULE,width:0.75},rectRadius:0.05});
  s.addText(n,{x:M+3.65+i*1.62,y:6.3,w:1.5,h:0.33,fontFace:MONO,fontSize:10.5,color:INK,align:'center',valign:'middle',margin:0});
});
s.addText('information_schema + sqlglot — DuckDB needs no credentials, so the path is proven without one',
  {x:M+10.2,y:6.32,w:2.2,h:0.3,fontFace:SANS,fontSize:8.5,color:MUTED,margin:0});
mark(s,'batch_yield_pct');
s.addNotes('DuckDB is the working warehouse and needs no credentials; connectors for Snowflake, Databricks, Redshift and Athena ship alongside it, unit-tested but never run against a live account.');

/* 8 — honesty */
s=p.addSlide(); dark(s);
s.addText('What it refuses to claim',{x:M,y:0.55,w:W-2*M,h:0.7,fontFace:SERIF,fontSize:34,bold:true,color:WHITE,margin:0});
s.addText('The feature that is hardest to demo and matters most in a regulated industry',
  {x:M,y:1.25,w:W-2*M,h:0.4,fontFace:SANS,fontSize:13,color:'93A2A9',margin:0});
const h=[['Says what it did not read','Roles, calculation groups, perspectives and translations are counted and named. An unread calculation group means a measure\'s documented expression is not the whole story — and the reader is told.'],
         ['Marks what it inferred','A statement derived from structure rather than declared by the model is flagged low-confidence and queued, never asserted quietly.'],
         ['Lets a sign-off expire','A decision is bound to the fingerprints it was made against. Change the logic and it goes stale by itself — an approved column would carry it over silently.']];
h.forEach(([t,b],i)=>{
  const x=M+i*4.05;
  s.addShape(p.ShapeType.roundRect,{x,y:2.0,w:3.75,h:3.5,fill:{color:'171E22'},line:{color:'2A343A',width:0.75},rectRadius:0.04});
  s.addShape(p.ShapeType.ellipse,{x:x+0.3,y:2.3,w:0.36,h:0.36,fill:{color:ACCENT}});
  s.addText(t,{x:x+0.3,y:2.82,w:3.15,h:0.75,fontFace:SANS,fontSize:14.5,bold:true,color:WHITE,margin:0});
  s.addText(b,{x:x+0.3,y:3.6,w:3.15,h:1.75,fontFace:SANS,fontSize:11.5,color:'93A2A9',margin:0,lineSpacingMultiple:1.2});
});
s.addText('A tool that overstates what it knows is worse than no tool, because someone will act on it. Every limit above is reported by the software itself, on screen, not written on this slide.',
  {x:M,y:5.85,w:W-2*M,h:0.7,fontFace:SANS,fontSize:13,italic:true,color:'C2CDD2',margin:0});
mark(s,'ee32bfb1016c',true);
s.addNotes('This is the differentiator. Anything can generate a document; the question is whether a reviewer can trust it without auditing it first.');

/* 9 — impact */
s=p.addSlide(); light(s);
title(s,'What it changes for the team','measured against what the tool actually produces today');
const rows=[['Writing a BRD/FRD','Typed by hand from interviews','Derived in seconds — 137 requirements from the largest fixture model'],
            ['Checking it is still true','Nothing checks; drift found in a meeting','Fingerprint comparison names the requirements in question'],
            ['A Power BI / warehouse dispute','Manual, expression by expression','Every shared metric compared, differences named'],
            ['Audit evidence','Assembled by hand after the fact','Every statement already carries its object and hash']];
s.addText('Task',{x:M+0.3,y:1.95,w:3.0,h:0.3,fontFace:SANS,fontSize:11,bold:true,color:FAINT,charSpacing:1.2,margin:0});
s.addText('Today',{x:M+3.5,y:1.95,w:3.8,h:0.3,fontFace:SANS,fontSize:11,bold:true,color:FAINT,charSpacing:1.2,margin:0});
s.addText('With Concordance',{x:M+7.5,y:1.95,w:4.3,h:0.3,fontFace:SANS,fontSize:11,bold:true,color:ACCENT,charSpacing:1.2,margin:0});
rows.forEach(([a,b,c],i)=>{
  const y=2.35+i*1.02;
  s.addShape(p.ShapeType.roundRect,{x:M,y,w:W-2*M,h:0.88,fill:{color:WHITE},line:{color:RULE,width:0.75},rectRadius:0.03});
  s.addText(a,{x:M+0.3,y:y+0.24,w:3.1,h:0.45,fontFace:SANS,fontSize:12.5,bold:true,color:INK,margin:0});
  s.addText(b,{x:M+3.5,y:y+0.24,w:3.8,h:0.45,fontFace:SANS,fontSize:11.5,color:MUTED,margin:0});
  s.addText(c,{x:M+7.5,y:y+0.18,w:4.4,h:0.6,fontFace:SANS,fontSize:11.5,color:INK,margin:0});
});
s.addText('The saving is real but secondary. The point is that the document can be re-checked at any time, which is not a thing that gets faster — it is a thing that was not possible.',
  {x:M,y:6.5,w:W-2*M,h:0.5,fontFace:SANS,fontSize:12.5,italic:true,color:MUTED,margin:0});
mark(s,'REQ-F-6c8ef6');
s.addNotes('Deliberately no invented percentages. Every figure here is something the tool produced.');

/* 10 — honest status */
s=p.addSlide(); light(s);
title(s,'Where it stands, honestly','what is proven, what is written but unproven, and what is not built');
card(s,M,1.95,3.85,3.5,'Proven end to end','Both Power BI formats. 797 tests. Drift, reconciliation against DuckDB, lineage to source, RLS and calculation-group logic, the 15-tool chatbot and the decision log — all verified against real models. Several bugs here were found that way, not by unit tests.',OKBG);
card(s,M+4.05,1.95,3.85,3.5,'Written, not yet live','Connectors for Snowflake, Databricks, Redshift and Athena share the DuckDB code path and are unit-tested against a DB-API cursor — parameters, per-platform identifier case folding, SQL dialect. None has authenticated to a live account; the sandbox blocks those hosts, confirmed rather than assumed.',WARNBG);
card(s,M+8.1,1.95,3.85,3.5,'Not built','No write path back into Power BI — Concordance reads and reports, it never edits a model. Report-page usage is outside the semantic layer, so a measure no other measure uses cannot be proven unused.',SOFT);
s.addShape(p.ShapeType.roundRect,{x:M,y:5.7,w:W-2*M,h:1.05,fill:{color:INK},line:{color:INK},rectRadius:0.03});
s.addText('Every one of these limits is stated by the software at the point of use, not only on this slide. That is the same standard the tool applies to the models it reads.',
  {x:M+0.35,y:5.95,w:W-2*M-0.7,h:0.6,fontFace:SANS,fontSize:13,color:'C2CDD2',margin:0});
mark(s,'tablePermission');
s.addNotes('Saying this out loud is a deliberate choice and consistent with the product claim.');

/* 11 — close */
s=p.addSlide(); dark(s);
s.addText('Concordance',{x:M,y:1.9,w:8.6,h:0.9,fontFace:SERIF,fontSize:44,bold:true,color:WHITE,margin:0});
s.addText('The document, the model and the warehouse — held together by something that breaks loudly when they diverge.',
  {x:M,y:2.9,w:8.6,h:1.0,fontFace:SANS,fontSize:18,color:'C2CDD2',margin:0,lineSpacingMultiple:1.2});
[['Innovation','A requirement bound to a fingerprint; a sign-off that expires on its own'],
 ['Technical','Lexer-based canonicalisation, a real AST for SQL, 797 tests, no regex'],
 ['Impact','Documents that can be re-checked, and disagreements found before a meeting']].forEach(([t,b],i)=>{
  const y=4.25+i*0.78;
  s.addShape(p.ShapeType.ellipse,{x:M,y:y+0.06,w:0.3,h:0.3,fill:{color:ACCENT}});
  s.addText(t,{x:M+0.5,y,w:2.1,h:0.4,fontFace:SANS,fontSize:13.5,bold:true,color:WHITE,margin:0});
  s.addText(b,{x:M+2.6,y:y+0.02,w:9.0,h:0.5,fontFace:SANS,fontSize:12.5,color:'93A2A9',margin:0});
});
mark(s,'concordance',true);
s.addNotes('Close on the mechanism, not the feature list.');

p.writeFile({fileName:'Concordance.pptx'}).then(f=>console.log('wrote',f));

"""Generic unsigned coaching PDF; no proprietary employer form is distributed."""
import json
import sys
from io import BytesIO
from xml.sax.saxutils import escape

FIELDS=('employee_name','location','role','leader_name','conversation_date','shift',
        'coaching_type','values','situation','impact','discussion','expectations',
        'leader_support','resources','follow_up','follow_up_required','follow_up_date','follow_up_owner')


def render(fields):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import letter
    if not isinstance(fields,dict) or set(fields)-set(FIELDS)-{'_fictional_sample'}:
        raise ValueError('Unsupported coaching fields.')
    output=BytesIO();styles=getSampleStyleSheet()
    label='FICTIONAL SAMPLE - NOT AN EMPLOYEE RECORD' if fields.get('_fictional_sample') else 'DRAFT - MANAGER / HR REVIEW REQUIRED'
    flow=[Paragraph('Leadership Coaching - Review Draft',styles['Title']),Paragraph(label,styles['Heading2'])]
    for key in FIELDS:
        value=fields.get(key,'Not provided')
        if isinstance(value,list):value=', '.join(str(v) for v in value)
        if not isinstance(value,str) or len(value)>1600:raise ValueError('Coaching text exceeds bounds.')
        flow.extend([Paragraph(escape(key.replace('_',' ').title()),styles['Heading3']),
                     Paragraph(escape(value).replace('\n','<br/>'),styles['BodyText']),Spacer(1,5)])
    flow.append(Paragraph('Employee signature: ____________________   Date: __________<br/>Leader signature: ____________________   Date: __________',styles['BodyText']))
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont('Helvetica',8)
        canvas.drawString(36,22,label);canvas.drawRightString(576,22,str(doc.page));canvas.restoreState()
    SimpleDocTemplate(output,pagesize=letter,rightMargin=36,leftMargin=36,topMargin=36,bottomMargin=40).build(flow,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()


if __name__=='__main__':
    raw=sys.stdin.buffer.read(32769)
    if len(raw)>32768:raise SystemExit('Coaching payload exceeds bound.')
    try:pdf=render(json.loads(raw))
    except (ImportError,ValueError,TypeError):raise SystemExit('PDF unavailable; follow optional PDF setup or review draft text.') from None
    sys.stdout.buffer.write(pdf)

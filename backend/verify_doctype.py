"""Does the document classifier get real documents right, and abstain otherwise?

Measured on realistic text for each type, plus the cases where it must say
nothing: a shopping list, a photo of a whiteboard, an empty scan. A classifier
that labels everything is worse than none, because a confident wrong label
gets filed and then cannot be found.

Reports precision and recall rather than a pass mark alone, per sections 18
and 70 — the numbers are the evidence.
"""
from app import doctype

FAIL = []


def check(ok, label, extra=''):
    print('  %-52s %s %s' % (label, 'PASS' if ok else 'FAIL', extra))
    if not ok:
        FAIL.append(label)


# Realistic first-page text. Invented companies and numbers throughout.
SAMPLES = [
    ("invoice", """TAX INVOICE
    Vertex Fuels Private Limited        GSTIN 29ABCDE1234F1Z5
    Invoice Number: INV-2026-00123      Due Date: 30 Sep 2026
    Bill To: Priya Sharma
    HSN 2710   Petrol 32.00 L @ 93.25
    Subtotal 2,984.00     Amount Due Rs 3,120.00"""),

    ("receipt", """GREEN BASKET
    Willow Road, Bengaluru
    Toor dal 1kg              148.00
    Milk 500ml                 69.60
    QTY 6                    Terminal ID 44812
    Cash Tendered 1000.00    Change Due 80.40
    THANK YOU FOR YOUR PURCHASE"""),

    ("bank_statement", """NORTHLINE BANK
    Statement of Account
    A/C 0004471203      IFSC NRTH0000447
    Opening Balance      12,480.00
    04 Sep  Withdrawal    2,000.00
    11 Sep  Deposit      18,500.00
    Closing Balance      28,980.00"""),

    ("payslip", """Salary Slip - September 2026
    Heavypoint Design Studio
    Basic Pay            68,000.00
    HRA                  27,200.00
    Gross Earnings     1,18,000.00
    Deductions: Provident Fund 8,160.00
    Net Pay            1,02,340.00"""),

    ("insurance_policy", """LUMEN LIFE
    Policy Schedule
    Policy Number: LL-4471203
    Sum Assured  Rs 1,00,00,000
    Premium      Rs 21,600 / year
    Nominee: Arjun Sharma
    The cover described above is subject to the terms and conditions."""),

    ("contract", """LEAVE AND LICENCE AGREEMENT
    THIS AGREEMENT is made at Bengaluru on 29 March 2026
    BETWEEN M. Menon (hereinafter called the Lessor)
    AND Priya Sharma (hereinafter called the Lessee)
    WITNESSETH that the Lessor hereby grants
    IN WITNESS WHEREOF the parties have set their hands."""),

    ("passport", """REPUBLIC OF INDIA
    PASSPORT
    Nationality: INDIAN
    Place of Issue: BENGALURU
    P<INDSHARMA<<PRIYA<<<<<<<<<<<<<<<<<<<<<<<<<<"""),

    ("id_card", """Unique Identification Authority
    AADHAAR
    Date of Birth: 12/04/1991
    Priya Sharma"""),

    ("certificate", """BOARD OF SECONDARY EDUCATION
    This is hereby certified that Priya Sharma
    has successfully completed the course
    Marks Sheet enclosed.  Awarded to Priya Sharma"""),

    ("resume", """PRIYA SHARMA
    Curriculum Vitae
    Career Objective: to work on systems that people rely on.
    Work Experience: Heavypoint Design Studio, 2019-2026
    Education: B.E. Computer Science
    Skills: Python, SQL    References available on request."""),

    ("ticket", """BOARDING PASS
    PNR: 4QXZ7T
    Departure 18:40   Seat No 14C   Coach B
    E-Ticket - please carry photo identification"""),

    ("prescription", """Dr A. Rao, MBBS
    PRESCRIPTION
    Rx
    Diagnosis: seasonal allergic rhinitis
    Cetirizine 10mg tablets - Dosage: one twice a day"""),

    ("utility_bill", """NORTHLINE POWER
    Electricity Bill
    Consumer No: AC-4471-2209
    Meter Reading 44821   Units Consumed 282
    Billing Period Aug 2026
    Rs 3,420.00"""),

    ("tax", """FORM 16
    Certificate under section 203 of the Income Tax Act
    Assessment Year 2026-27
    PAN: ABCDE1234F      TDS deducted 48,200"""),
]

# Things that are NOT any of these, and must not be labelled.
ABSTAIN = [
    ("shopping list", "milk\nbread\nbananas\ncoffee\nwashing powder"),
    ("whiteboard photo", "sprint 4 ideas\n- faster search\n- fix the thing\n- ask Ravi"),
    ("empty scan", "   "),
    ("gibberish ocr", "l1 ll1 |||  1l1| ,,, ...  ~~~"),
    ("a short note", "back at 6"),
]

print('  --- each type, on realistic first-page text ---')
correct = 0
wrong = []
for expected, text in SAMPLES:
    got = doctype.classify(text)
    ok = got['kind'] == expected
    correct += ok
    if not ok:
        wrong.append((expected, got['kind'], got['score'], got['evidence'][:3]))
    print('  %-18s -> %-18s %s  conf=%.2f' %
          (expected, got['kind'], 'ok ' if ok else 'MISS', got['confidence']))

print()
print('  --- must abstain ---')
abstained = 0
false_pos = []
for label, text in ABSTAIN:
    got = doctype.classify(text)
    ok = got['kind'] is None
    abstained += ok
    if not ok:
        false_pos.append((label, got['kind'], got['score']))
    print('  %-18s -> %-18s %s' % (label, got['kind'], 'ok ' if ok else 'FALSE POSITIVE'))

n, a = len(SAMPLES), len(ABSTAIN)
recall = correct / n
precision = correct / (correct + len(false_pos)) if (correct + len(false_pos)) else 0.0

print()
print('  --- measured, on %d documents and %d non-documents ---' % (n, a))
print('    correctly typed      %d/%d' % (correct, n))
print('    correctly abstained  %d/%d' % (abstained, a))
print('    recall               %.2f' % recall)
print('    precision            %.2f' % precision)
if wrong:
    print('    misses:')
    for e, g, sc, ev in wrong:
        print('      %s read as %s (score %d) %s' % (e, g, sc, ev))
if false_pos:
    print('    false positives:')
    for l, g, sc in false_pos:
        print('      %s read as %s (score %d)' % (l, g, sc))

print()
check(recall >= 0.80, 'recall at or above 0.80', '%.2f' % recall)
check(len(false_pos) == 0, 'nothing that is not a document was labelled')
check(all(doctype.classify(t)['confidence'] <= 0.95 for _, t in SAMPLES),
      'confidence never claims certainty')
check(doctype.classify(None)['kind'] is None, 'None input is handled')
check(doctype.classify('')['kind'] is None, 'empty input is handled')

# Evidence must be returned, or a wrong answer cannot be argued with.
ev = doctype.classify(SAMPLES[0][1])['evidence']
check(bool(ev) and 'tax invoice' in ev, 'the evidence names what it matched on', ev[:3])

print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')

"""Regression tests for four variable-layout examples and earlier two PDFs.
Synthetic PDFs contain embedded text modeled on the four supplied screenshots.
"""
import unittest
from pathlib import Path
import fitz
from extractor import extract_strength_schedules
from rebar import extract_rebar_dynamic

ROOT=Path(__file__).parent
ORIGINALS=(Path('/mnt/data/2026-09-11_202 EAST 120TH ST_FO_S&S.pdf'),Path('/mnt/data/2026-09-11_202 EAST 120TH ST_SS_S&S.pdf'))


def make_page(kind):
    d=fitz.open();p=d.new_page(width=900,height=900)
    x_label=76;x_value=610;y=130
    def put(x,y,s):p.insert_text((x,y),s,fontsize=11,fontname='helv')
    if kind!=3:
        put(25,75,'2.');put(76,75,'CONCRETE SHALL HAVE A MIN. COMPRESSIVE STRENGTH AT 28 DAYS AS FOLLOWS, UNLESS');put(76,95,'OTHERWISE NOTED ON THE DRAWINGS:')
    items={
      1:[('A','SLAB ON GRADE:',4000),('B','FOUNDATION WALLS, SHEAR WALLS,',6000),('C','FRAMED SLABS AND BEAMS:',6000),('D','CONCRETE TOPPING AND PAD:',3000),('E','OTHER NON STRUCTURAL ELEMENTS:',3000),('F','ALL OTHER CONCRETE:',4000)],
      2:[('A','MAT:',8000),('B','FOUNDATION WALL:',6000),('C','SHEAR WALLS, AND COLUMNS (CELLAR TO 10TH FLOOR):',10000),('D','SHEAR WALLS, AND COLUMNS (11TH FLOOR TO ROOF):',8000),('E','FRAMED SLABS AND BEAMS (CELLAR TO 10TH FLOOR):',8000),('F','FRAMED SLABS AND BEAMS (11TH FLOOR TO ROOF):',6000),('G','CONCRETE TOPPING AND PAD:',3000),('H','OTHER NON STRUCTURAL ELEMENTS:',3000),('I','ALL OTHER CONCRETE:',4000)],
      3:[('A','MAT, FOUNDATION WALL:',8000),('B','SHEAR WALL, COLUMNS, SLABS, BEAMS, STAIRS:',None),('C','CONCRETE TOPPING AND PAD:',3000),('D','OTHER NON STRUCTURAL ELEMENTS:',3000),('E','ALL OTHER CONCRETE:',4000)],
      4:[('A','SLAB ON GRADE, FOOTINGS, GRADE BEAMS:',5000),('B','FOUNDATION WALLS, SHEAR WALLS, COLUMNS:',6000),('C','FRAMED SLABS, STAIRS, AND BEAMS:',6000),('D','CONCRETE TOPPING AND PAD:',3000),('E','OTHER NON STRUCTURAL ELEMENTS:',3000),('F','ALL OTHER CONCRETE:',4000)],
    }[kind]
    for letter,label,value in items:
        put(48,y,letter+'.');put(x_label,y,label)
        if value is not None:put(x_value,y,f'{value:,} PSI, NORMAL WEIGHT')
        if kind==1 and letter=='B':
            y+=17;put(x_label,y,'COLUMNS, FOOTINGS, GRADE BEAMS:')
        if kind==3 and letter=='B':
            y+=22;put(x_label+160,y,'1ST FLOOR TO 16TH FLOOR:');put(x_value,y,'10,000 PSI, NORMAL WEIGHT')
            y+=22;put(x_label+160,y,'16TH FLOOR THROUGH ROOF:');put(x_value,y,'8,000 PSI, NORMAL WEIGHT')
        y+=33
    put(25,y+35,'3.');put(x_label,y+35,'ALL CONCRETE EXPOSED TO FREEZE AND THAW SHALL HAVE AIR ENTRAINMENT')
    return d

class GeneralStrengthTests(unittest.TestCase):
    expected={
      1:[4000,6000,6000,3000,3000,4000],
      2:[8000,6000,10000,8000,8000,6000,3000,3000,4000],
      3:[8000,10000,8000,3000,3000,4000],
      4:[5000,6000,6000,3000,3000,4000],
    }
    def test_four_supplied_layouts(self):
        for kind,expected in self.expected.items():
            with self.subTest(layout=kind):
                doc=make_page(kind)
                got,warnings=extract_strength_schedules(doc[0],f'example{kind}.pdf','A-001')
                self.assertEqual([r['strength_psi'] for r in got],expected,(got,warnings))
                if kind==1:
                    self.assertIn('COLUMNS, FOOTINGS, GRADE BEAMS',got[1]['element'])
                if kind==2:
                    self.assertIn('CELLAR TO 10TH FLOOR',got[2]['level_range'])
                if kind==3:
                    self.assertEqual([r['level_range'] for r in got if r['letter']=='B'],['1ST FLOOR TO 16TH FLOOR','16TH FLOOR THROUGH ROOF'])
                doc.close()

    def test_different_rebar_strength_headings(self):
        # A different project's 4/5/6-ksi lap schedule should preserve those
        # actual headings, never invent 8/10-ksi columns from the old project.
        doc=fitz.open();p=doc.new_page(width=1000,height=700)
        def put(x,y,s):p.insert_text((x,y),s,fontsize=10)
        put(300,60,"LAP SPLICE LENGTH 'Ls' (in)")
        for x,v in zip((268,430,590),(4000,5000,6000)):
            put(x,100,f'{v:,} PSI')
        for y,bar in ((160,'#3'),(180,'#4'),(200,'#5')):
            put(200,y,bar)
            for k,x in enumerate((250,290,350,410,450,510,570,610,670)):
                put(x,y,str(12+k))
        rows,issues=extract_rebar_dynamic(p,'different_rebar.pdf','S-101')
        self.assertEqual(len(rows),9,issues)
        self.assertEqual(sorted({r['concrete_psi'] for r in rows}),[4000,5000,6000])
        self.assertEqual(rows[0]['tension_typ_in'],12)
        doc.close()

    def test_original_uploaded_drawings(self):
        for path in ORIGINALS:
            if not path.exists():continue
            with self.subTest(path=path.name):
                d=fitz.open(path)
                rows,warnings=extract_strength_schedules(d[2],path.name,'FO-003' if '_FO_' in path.name else 'S-003')
                self.assertEqual([r['strength_psi'] for r in rows],[4000,6000,10000,8000,8000,6000,3000,3000,4000],warnings)
                d.close()

if __name__ == '__main__':unittest.main(verbosity=2)

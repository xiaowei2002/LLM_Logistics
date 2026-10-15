from __future__ import annotations
import csv
import json
import tempfile
import unittest
from pathlib import Path
from core.graph.olist_preparation import FILES, OlistPreparationError, prepare_olist_snapshot


class OlistPreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'raw';self.source.mkdir()
        self.data={
            'orders':[{'order_id':'o1','order_status':'processing','order_purchase_timestamp':'2018-01-07 23:59:59'},
                      {'order_id':'o2','order_status':'canceled','order_purchase_timestamp':'2018-01-08 00:00:00'},
                      {'order_id':'o3','order_status':'unavailable','order_purchase_timestamp':'2018-01-08 00:00:00'}],
            'order_items':[{'order_id':'o1','order_item_id':'1','product_id':'p1','price':'10.00','freight_value':'0.00'},
                           {'order_id':'o1','order_item_id':'2','product_id':'p1','price':'10.00','freight_value':'0.00'},
                           {'order_id':'o2','order_item_id':'1','product_id':'p2','price':'20.00','freight_value':'1.00'}],
            'products':[{'product_id':'p1','product_category_name':'cat1'},
                        {'product_id':'p2','product_category_name':''}]}
        self.write_sources()

    def write_sources(self):
        for name,rows in self.data.items():
            with (self.source/FILES[name]).open('w',encoding='utf-8',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

    def run_prep(self):
        return prepare_olist_snapshot(self.source,self.root/'prepared')

    def test_preserves_repeated_product_and_final_status(self):
        r=self.run_prep()
        with Path(r['facts_file']).open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
        self.assertEqual([(x['order_id'],x['order_item_id']) for x in rows],[('o1','1'),('o1','2'),('o2','1')])
        self.assertEqual(r['item_count_sum'],3)
        self.assertEqual([x['week_start'] for x in rows],['2018-01-01','2018-01-01','2018-01-08'])
        self.assertEqual(rows[2]['order_status'],'canceled')
        self.assertEqual(rows[2]['product_category_name'],'')
        self.assertEqual(rows[2]['category_key'],'__UNKNOWN_CATEGORY__')
        self.assertEqual(rows[0]['price'],'10.00')
        self.assertEqual(r['unknown_category_item_records'],1)
        self.assertTrue(any(x['type']=='orders_without_items' for x in r['cleaning_gate']['warnings']))
        for name,file in FILES.items():
            self.assertEqual((self.source/file).read_bytes(),Path(r['cleaned_files'][name]).read_bytes())

    def test_duplicate_item_key_blocked(self):
        self.data['order_items'][1]['order_item_id']='1';self.write_sources()
        with self.assertRaises(OlistPreparationError):self.run_prep()
        self.assertFalse((self.root/'prepared').exists())

    def test_orphan_product_blocked(self):
        self.data['order_items'][0]['product_id']='absent';self.write_sources()
        with self.assertRaises(OlistPreparationError):self.run_prep()

    def test_invalid_purchase_blocked(self):
        self.data['orders'][0]['order_purchase_timestamp']='not-a-time';self.write_sources()
        with self.assertRaises(OlistPreparationError):self.run_prep()

    def test_nonfinite_price_blocked(self):
        self.data['order_items'][0]['price']='inf';self.write_sources()
        with self.assertRaises(OlistPreparationError):self.run_prep()

    def test_duplicate_product_does_not_expand_join(self):
        self.data['products'].append(dict(self.data['products'][0]));self.write_sources()
        with self.assertRaises(OlistPreparationError):self.run_prep()

    def test_existing_output_is_preserved(self):
        out=self.root/'prepared';out.mkdir();sentinel=out/'keep.txt';sentinel.write_text('keep')
        with self.assertRaises(FileExistsError):self.run_prep()
        self.assertEqual(sentinel.read_text(),'keep')

    def test_input_directory_cannot_be_output(self):
        with self.assertRaises(OlistPreparationError):prepare_olist_snapshot(self.source,self.source)


if __name__=='__main__':
    unittest.main()

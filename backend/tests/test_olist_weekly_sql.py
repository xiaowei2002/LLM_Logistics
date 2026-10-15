import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from core.tools.olist_weekly_sql import (build_weekly_dataset, open_readonly, query,
    TOP_SQL, WEEK_SQL, REQUIRED, sha, START, TRAIN_END, END, UNKNOWN)


class OlistWeeklySQLTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.prep = self.root/'prep'
        self.prep.mkdir()
        rows = []
        # Five train categories; huge future-only category must never be selected.
        for cat, number in [('a',5),('b',4),('c',3),('d',2),('e',1),('future',0)]:
            for n in range(number):
                rows.append(self.row(cat, START, len(rows)))
        for n in range(50):
            rows.append(self.row('future', TRAIN_END, len(rows)))
        rows.append(self.row('', START, len(rows)))
        rows.append(self.row('a', END, len(rows)))  # upper bound excluded
        self.data = pd.DataFrame(rows, columns=REQUIRED)
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def row(self, cat, week, n):
        return dict(order_id=f'order{n}', order_item_id='1', product_id=f'p{n}',
            order_status='canceled', order_purchase_timestamp=week+' 12:00:00',
            product_category_name=cat, category_key=cat or UNKNOWN,
            category_missing=str(int(not cat)), week_start=week, item_count='1',
            price='1.00', freight_value='0.00')

    def write(self):
        path = self.prep/'order_item_facts.csv'
        self.data.to_csv(path, index=False)
        self.summary = dict(preparation_version='olist_preparation_v1', status='success',
            join_preserved_item_grain=True, facts_sha256=sha(path),
            facts_rows=len(self.data), item_count_sum=len(self.data))
        self.write_summary()

    def write_summary(self):
        (self.prep/'preparation_summary.json').write_text(json.dumps(self.summary))

    def build(self):
        return build_weekly_dataset(self.prep, self.root/'out')

    def test_time_split_train_only_selection_zero_grid_and_unknown(self):
        result = self.build()
        self.assertEqual([r['category_key'] for r in result['selected_categories']], list('abcde'))
        self.assertEqual(result['split_rows'], {'train':300,'validation':60,'test':70})
        train = pd.read_csv(self.root/'out'/'weekly_train.csv')
        self.assertEqual(train.observed_item_count.sum(), 15)
        self.assertEqual(int(train[train.category_key.eq('a')].iloc[0].observed_item_count), 5)
        self.assertEqual(result['unknown_items_in_window'], 1)
        self.assertEqual(len(train[train.observed_item_count.eq(0)]), 295)
        evidence = json.loads((self.root/'out'/'sql_evidence.json').read_text())
        self.assertNotIn(END, {r['week_start'] for r in evidence['weekly']['rows']})
        for split, low, high in [('train',START,TRAIN_END),('validation',TRAIN_END,'2018-05-21'),('test','2018-05-21',END)]:
            part = pd.read_csv(self.root/'out'/f'weekly_{split}.csv')
            self.assertTrue(part.week_start.ge(low).all() and part.week_start.lt(high).all())

    def test_tampered_facts_rejected(self):
        with (self.prep/'order_item_facts.csv').open('a') as file:
            file.write('\n')
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.build()
        self.assertFalse((self.root/'out').exists())

    def test_bad_week_rejected_even_with_updated_hash(self):
        self.data.loc[0,'week_start'] = '2017-01-03'
        self.write()
        with self.assertRaisesRegex(ValueError, 'week_start'):
            self.build()

    def test_invalid_item_counts_rejected(self):
        self.data.loc[0,'item_count'] = '2'
        self.write()
        with self.assertRaisesRegex(ValueError, 'item_count'):
            self.build()

    def test_duplicate_item_keys_rejected(self):
        self.data = pd.concat([self.data,self.data.iloc[[0]]], ignore_index=True)
        self.write()
        with self.assertRaisesRegex(ValueError, 'keys'):
            self.build()

    def test_category_marker_rejected(self):
        self.data.loc[0,'category_key'] = 'fake'
        self.write()
        with self.assertRaisesRegex(ValueError, 'Category'):
            self.build()

    def test_existing_output_and_source_subdirectory_rejected(self):
        out = self.root/'out'
        out.mkdir()
        (out/'sentinel').write_text('keep')
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual((out/'sentinel').read_text(),'keep')
        with self.assertRaises(ValueError):
            build_weekly_dataset(self.prep, self.prep/'nested')

    def test_readonly_blocks_mutation_schema_attach_and_unknown_templates(self):
        self.build()
        db = self.root/'out'/'olist_snapshot.sqlite3'
        before = sha(db)
        con = open_readonly(db)
        try:
            self.assertEqual(len(query(con, TOP_SQL,(START,TRAIN_END,UNKNOWN,5))),5)
            for sql in ["DELETE FROM order_item_facts", 'CREATE TABLE x(a)',
                        "ATTACH DATABASE ':memory:' AS other", 'PRAGMA query_only=OFF',
                        'SELECT name FROM sqlite_master', "SELECT load_extension('x')"]:
                with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                    con.execute(sql).fetchall()
            with self.assertRaises(ValueError):
                query(con, 'SELECT * FROM order_item_facts', ())
            # A category value containing SQL syntax stays a literal parameter.
            self.assertEqual(query(con, TOP_SQL,(START,TRAIN_END,"x' OR 1=1 --",5))[0]['category_key'],'a')
        finally:
            con.close()
        self.assertEqual(sha(db),before)

    def test_manifest_count_mismatch_rejected(self):
        self.summary['facts_rows'] += 1
        self.write_summary()
        with self.assertRaisesRegex(ValueError, 'counts'):
            self.build()

    def test_insufficient_training_categories_fail_without_publishing_output(self):
        self.data = self.data[~self.data.category_key.eq('e')]
        self.write()
        with self.assertRaisesRegex(ValueError,'Fewer than five'):
            self.build()
        self.assertFalse((self.root/'out').exists())


if __name__ == '__main__':
    unittest.main()

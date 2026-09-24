import unittest, json, re
from scripts.build import embed


class BuildTests(unittest.TestCase):
    def test_embedded_data_cannot_close_script_or_insert_markup(self):
        template = '<script id="catalog-data" type="application/json">{}</script><p>keep</p>'
        data = {'entries':[{'name':'</script><img src=x onerror=alert(1)>'}]}
        html = embed(template, data)
        self.assertEqual(html.count('</script>'), 1)
        payload = re.search(r'application/json">(.*?)</script>', html, re.S).group(1)
        self.assertEqual(json.loads(payload), data)
        self.assertTrue(html.endswith('<p>keep</p>'))

    def test_missing_marker_is_error_not_silent_stale_output(self):
        with self.assertRaises(ValueError): embed('<p>hello</p>', {'entries':[]})

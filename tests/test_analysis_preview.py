import unittest
from unittest.mock import AsyncMock, patch

import test_core
from app.main import app


class AnalysisPreviewTests(test_core.APITests):
    test_auth_and_csrf_cover_api_and_browser = None
    test_collection_job_persists_results = None
    test_edit_export_and_validation = None

    def test_preview_uses_ai_without_persisting_or_echoing_source_images(self):
        self.login()
        values = {'records': [{'url': 'https://www.instagram.com/p/PreviewCafe/',
                             'source': 'post', 'title': '카페', 'text': '커피'}]}
        output = [{'url': values['records'][0]['url'], 'category': '카페',
                   'classification': 'ai', 'media': [{'data_url': 'private-image-fixture'}],
                   'details': {'summary': '커피가 있는 카페'}}]
        with patch.object(app.state.ai, 'key', return_value='private-key-fixture'), patch(
            'app.main.analyze_records', new=AsyncMock(return_value=(output, ''))
        ) as analyze:
            response = self.client.post('/api/ai/preview', json=values,
                                        headers={'Origin': 'http://testserver'})
        self.assertEqual(response.status_code, 200)
        analyze.assert_awaited_once()
        self.assertEqual(response.json()['items'][0]['category'], '카페')
        self.assertNotIn('private-image-fixture', response.text)
        self.assertNotIn('private-key-fixture', response.text)
        self.assertEqual(app.state.store.total(), 0)
        self.assertFalse(app.state.busy)

    def test_preview_requires_auth_origin_ai_key_and_single_record(self):
        values = {'records': [{'url': 'https://www.instagram.com/p/PreviewCafe/', 'source': 'post'}]}
        headers = {'Origin': 'http://testserver'}
        self.assertEqual(self.client.post('/api/ai/preview', json=values, headers=headers).status_code, 401)
        self.login()
        self.assertEqual(self.client.post('/api/ai/preview', json=values,
                         headers={'Origin': 'https://wrong.example'}).status_code, 403)
        with patch.object(app.state.ai, 'key', return_value=None):
            self.assertEqual(self.client.post('/api/ai/preview', json=values, headers=headers).status_code, 422)
        values['records'] *= 2
        self.assertEqual(self.client.post('/api/ai/preview', json=values, headers=headers).status_code, 422)

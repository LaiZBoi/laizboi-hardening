"""
AI-powered receipt OCR for vehicle expense tracking.
Uses Claude vision to extract structured data from receipt images.
"""
import base64
import json
import logging

logger = logging.getLogger('vehicles')

SYSTEM_PROMPT = """You are a receipt data extraction assistant.
Extract expense data from receipt images and return ONLY valid JSON with no other text.
Be precise with amounts — always use numbers, never strings for monetary values.
If a field cannot be determined, use null."""

EXTRACT_PROMPT = """Extract the following from this receipt image and return as JSON:

{
  "vendor": "store/business name (string or null)",
  "date": "date in YYYY-MM-DD format (string or null)",
  "amount": total amount paid as number (e.g. 45.20, not "$45.20"),
  "tax_amount": tax amount as number or null,
  "category": one of: "fuel", "maintenance", "repair", "insurance", "registration", "toll", "cleaning", "inspection", "other",
  "odometer": odometer/mileage reading as integer if shown on receipt, else null,
  "description": "brief summary of items purchased (string or null)",
  "confidence": "high" if data is clear, "medium" if some fields are uncertain, "low" if image is poor quality
}

Category guidance:
- fuel: gas station, petrol, diesel purchases
- maintenance: oil change, filters, routine service
- repair: mechanic, parts, bodywork, tire replacement
- insurance: insurance payment or premium
- registration: DMV, license plate, registration fees
- toll: toll roads, parking, bridge fees
- cleaning: car wash, detailing
- inspection: emissions test, safety inspection

Return ONLY the JSON object, no explanation."""


def extract_receipt_data(image_file):
    """
    Use Claude vision to extract structured data from a receipt image.

    Args:
        image_file: Django InMemoryUploadedFile or similar file object

    Returns:
        dict with keys: success, data (if success), error (if failure)
        data contains: vendor, date, amount, tax_amount, category,
                       odometer, description, confidence
    """
    from django.conf import settings

    api_key = getattr(settings, 'ANTHROPIC_API_KEY', None)
    if not api_key:
        return {'success': False, 'error': 'Anthropic API key not configured. Set it in Settings → AI.'}

    # Read and encode image
    try:
        image_file.seek(0)
        image_data = image_file.read()
        b64_data = base64.standard_b64encode(image_data).decode('utf-8')
    except Exception as e:
        logger.error(f'[receipt_ocr] Failed to read image: {e}')
        return {'success': False, 'error': f'Could not read image file: {e}'}

    # Determine media type
    content_type = getattr(image_file, 'content_type', 'image/jpeg')
    if content_type not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'):
        content_type = 'image/jpeg'

    # Call Claude
    try:
        import anthropic
        model = getattr(settings, 'CLAUDE_MODEL', 'claude-sonnet-4-6')
        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model=model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': content_type,
                            'data': b64_data,
                        },
                    },
                    {
                        'type': 'text',
                        'text': EXTRACT_PROMPT,
                    },
                ],
            }],
        )

        raw_text = ''
        for block in response.content:
            if hasattr(block, 'text'):
                raw_text += block.text

        # Parse JSON from response
        raw_text = raw_text.strip()
        # Strip markdown code fences if present
        if raw_text.startswith('```'):
            raw_text = raw_text.split('```')[1]
            if raw_text.startswith('json'):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        data = json.loads(raw_text)

        # Sanitise types
        if data.get('amount') is not None:
            try:
                data['amount'] = float(data['amount'])
            except (TypeError, ValueError):
                data['amount'] = None
        if data.get('tax_amount') is not None:
            try:
                data['tax_amount'] = float(data['tax_amount'])
            except (TypeError, ValueError):
                data['tax_amount'] = None
        if data.get('odometer') is not None:
            try:
                data['odometer'] = int(data['odometer'])
            except (TypeError, ValueError):
                data['odometer'] = None

        valid_categories = {
            'fuel', 'maintenance', 'repair', 'insurance',
            'registration', 'toll', 'cleaning', 'inspection', 'other',
        }
        if data.get('category') not in valid_categories:
            data['category'] = 'other'

        return {'success': True, 'data': data}

    except json.JSONDecodeError as e:
        logger.warning(f'[receipt_ocr] JSON parse error: {e} | raw: {raw_text[:200]}')
        return {'success': False, 'error': 'Could not parse AI response. Try a clearer image.'}
    except Exception as e:
        logger.error(f'[receipt_ocr] Claude API error: {e}')
        return {'success': False, 'error': f'AI extraction failed: {e}'}

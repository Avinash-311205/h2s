import json
with open('app/resources/normalization_dictionary.json') as f:
    data = json.load(f)

languages = data.get('languages', [])
categories = data.get('categories', {})

print('Languages:', languages)
print('Categories:', len(categories))

total_subcats = 0
total_keywords = 0

for cat, cat_data in categories.items():
    subcats = cat_data.get('sub_categories', {})
    total_subcats += len(subcats)
    for subcat, subcat_data in subcats.items():
        keywords = subcat_data.get('keywords', {})
        for lang, kw_list in keywords.items():
            total_keywords += len(kw_list)

print('Subcategories:', total_subcats)
print('Total keywords:', total_keywords)

for cat in categories:
    print('  {}: {} subcats'.format(cat, len(categories[cat].get('sub_categories', {}))))
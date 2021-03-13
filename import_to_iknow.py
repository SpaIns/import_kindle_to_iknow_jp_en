import json # for reading response & our kindle data
import requests # for posting to iKnow
import urllib # For encoding strings to url strings
import pykakasi # For transliterating kanji->kana
import brotlicffi # For decompressing responses
from jp_kindle_lookup_to_json.kindle_to_json import create_json_from_db
import sys, re, signal

# Initialize kakasi
kks = pykakasi.kakasi()

# Setup the parts of speech that iKnow recognizes
valid_parts_of_speech = set({
    'verb', 'noun', 'phrase', 'adjective', 'adverb', 'phrasal verb',
    'particle', 'interjection', 'interrogative', 'conjunction', 'preposition',
    'adjectival noun', 'auxiliary verb', 'verbal noun', 'noun abbreviation', 
    'pronoun', 'proper noun', 'none',
    # and a few that aren't recognized by iKnow but come from Jisho we can map to recognizable
    'no-adjective', 'na-adjective', 'transitive verb', 'suru verb', 'intransitive verb',
    'prefix', 'i-adjective', 'suffix', 'ichidan verb', 'suru verb - special class',
})
# Map the recognized parts of speech to the code sent in the string
pos_map = {
    'verb' : 'V',
    'transitive verb': 'V',
    'intransitive verb': 'V',
    'suru verb': 'V',
    'suru verb - special class' : 'V',
    'ichidan verb': 'V',
    'noun': 'N',
    'phrase' : 'E',
    'adjective' : 'A',
    'i-adjective': 'A',
    'no-adjective': 'A',
    'na-adjective': 'A',
    'adverb' : 'D',
    'prefix': 'D', # taking a liberty here
    'suffix': 'D', # taking a liberty here
    'phrasal verb' : 'PH',
    'particle' : 'PL',
    'interjection' : 'I',
    'interrogative' : 'INT',
    'conjunction' : 'J',
    'preposition' : 'PR',
    'adjectival noun' : 'AN',
    'auxiliary verb' : 'VA',
    'verbal noun' : 'VN',
    'noun abbreviation' : 'NA',
    'pronoun' : 'NR',
    'proper noun': 'NP',
    'none': 'NONE'
}

# Define some constants
BAD_DEF = 'NO DEFINITION FOUND'
BAD_READING = 'NO READING FOUND'
# Store the words we add during this round
added = set()
# Store words we've added in prior runs (comes from prior_results.json)
previously_added = set()
# List of dicts storing info on word/samples we failed to add
failed_to_add = [] # contains dicts mapping {"course", "course_id", "word"}
failed_to_add_sample = [] # contains dicts mapping {"course", "course_id", "word", "word_id", "sentence"}
course_info = [] # contains dicts mapping {"title", "cur_course_id", "number", "items"}

# Used for when we decide to overwrite what ctrl+c does
original_sigint = signal.getsignal(signal.SIGINT)

def convert_json_to_items(cookie_string: str, csrf_token: str, import_json: str):
    # Try to open the prior results JSON file & add the already-added words to our
    # previously_added set to ensure we don't add the same words multiple times
    existing_courses = {} # Map of title to info as a tuple. cur id, cur #, cur items
    existing_course_titles = set() # Set of titles
    try:
        with open('prior_results.json', 'r', encoding='utf-8') as pr:
            json_data = json.load(pr)
            for word in json_data.get('added', []):
                previously_added.add(word)
            for course in json_data.get('courses', []):
                # Note: all these fields must exist. Hence the non-safe access, I want this to crash
                # now if the prior_results json is bad
                title = course['title']
                existing_course_titles.add(title)
                existing_courses[title] = (course['cur_course_id'], course['number'], course['items'])
            # We don't care about not-added or no-sample, since we would theoretically be retrying those
            # items here, assuming the same kindle_json file is provided.
            # If you care about the data there, save it before re-running!
    except FileNotFoundError:
        print('No prior results JSON found. Skipping.')
    
    # Setup a handler for ctrl+c such that we always generate the results json at this point
    # TODO: this doesn't actually seem to work as intended
    signal.signal(signal.SIGINT, create_results_json)

    # Define our headers that we'll re-use for most requests
    headers = {
        'Host': 'iknow.jp',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:86.0) Gecko/20100101 Firefox/86.0',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://iknow.jp/home',
        'X-CSRF-Token': csrf_token,
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Length': '265',
        'Origin': 'https://iknow.jp',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Cookie': cookie_string,
        'Sec-GPC': '1',
        'TE': 'Trailers',
    }

    with open(import_json, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
        for cur_book in json_data.get('books', []):
            cur_title = cur_book['title']
            # Grab info from JSON if exists, otherwise default to initialized values
            course_id, cur_course_counts, cur_item_count = existing_courses.get(title, ('', 0, 0))
            if course_id == '':
                # Need to make a new course and get the id for it.
                course_id = create_new_course(cur_title, cur_course_counts, headers)
                # Check return value to see if we actually made a new course successfully
                if course_id == '':
                    print('Unable to make a first course for ' + cur_title + '. Moving onto new book')
                    continue
            cur_course_info = {
                'title' : cur_title,
                'cur_course_id' : course_id,
                'number': cur_course_counts,
                'items': cur_item_count,
            }
            course_info.append(cur_course_info)
            book_words = cur_book['words']
            cur_course = cur_title + ' ' + str(cur_course_counts)
            # NOTE: This is probably a good place to implement threading, but I don't want to
            # hit iKnow too hard since the API isn't public and I don't want to get blocked
            for word in book_words:
                if cur_item_count >= 100:
                    # Create a new course and roll over.  iKnow recommends courses have a max of 100 items
                    cur_course_counts += 1
                    new_course_id = create_new_course(cur_title, cur_course_counts, headers)
                    # Check return value to see if we actually made a new course successfully
                    # If not, we'll try again the next run. TODO: Is this a good idea?
                    if new_course_id == '':
                        print('Unable to make a new course for ' + cur_title + '. Adding to existing course')
                        cur_course_counts -= 1
                    else:
                        course_id = new_course_id
                        cur_item_count = 0
                    cur_course = cur_title + ' ' + str(cur_course_counts)
                    # Update our course info map
                    cur_course_info['cur_course_id'] = course_id
                    cur_course_info['number'] = cur_course_counts

                # Create the JSON object to send
                word_id = create_new_item(cur_course, course_id, word, headers)
                if word_id == '':
                    # Couldn't create the item - move on to the next
                    continue
                cur_item_count += 1
                # In preperation for sending sample sentence, create the kana-only version of the sample
                trans = create_transliteration(word)
                # Only add sample sentence if we managed to transliterate something
                if trans == '':
                    no_sample_dict = {
                        'course': course,
                        'course_id': course_id,
                        'word': word['word'],
                        'word_id': word_id,
                        'sentence': word['sample'],
                    }
                    failed_to_add_sample.append(no_sample_dict)
                else:
                    add_sample_sentence(word, trans, cur_course, course_id, word_id, headers)
            # End of words loop
            # Finish updating our course info
            cur_course_info['cur_course_id'] = course_id
            cur_course_info['number'] = cur_course_counts
            cur_course_info['items'] = cur_item_count
        # End of books loop
    # Have added all words we wanted to from import_json
    create_results_json()

# Creates the final results json file
def create_results_json():
    # Remap the exit signal to default
    signal.signal(signal.SIGINT, original_sigint)
    print('Writing out results to prior_results.json')
    # Next lets create the JSON file for what words we've added and haven't
    results = {} # head of the json object we will write
    all_added = list(added)
    all_added.extend(list(previously_added))
    results['courses'] = course_info
    results['added'] = all_added
    results['not-added'] = failed_to_add
    results['no-sample'] = failed_to_add_sample
    with open('prior_results.json', 'w+', encoding='utf-8') as j:
        j.write(json.dumps(results, indent=4, ensure_ascii=False))
    # This should be the final call of this script.
    exit(0)

# Adds a sample sentence for a word already in iKnow
def add_sample_sentence(word: dict, trans: str, course: str, course_id: str, word_id: str, headers: dict) -> None:
    '''
And for the actual adding of the example sentence, here's the form:

utf8	"✓"
sentence_package[sentence][text]	"絵っと。。。鼻歌ね。。"
sentence_package[sentence][transliteration]	"え+っと+。+。+。+はなうた+ね+。+。"
sentence_package[sentence][language]	"ja"
sentence_package[translation][text]	""
sentence_package[translation][language]	"en"
sentence_package[sound][url]	""
sentence_package[image_url]	""
commit	"Add"

And here's a sample of the sent payload, split for legibility

utf8=%E2%9C%93&sentence_package%5Bsentence%5D%5Btext%5D=
    %E7%B5%B5%E3%81%A3%E3%81%A8%E3%80%82%E3%80%82%E3%80%82%E9%BC%BB%E6%AD%8C%E3%81%AD%E3%80%82%E3%80%82
&sentence_package%5Bsentence%5D%5Btransliteration%5D=
    %E3%81%88+%E3%81%A3%E3%81%A8+%E3%80%82+%E3%80%82+%E3%80%82+%E3%81%AF%E3%81%AA%E3%81%86%E3%81%9F+%E3%81%AD+%E3%80%82+%E3%80%82
&sentence_package%5Bsentence%5D%5Blanguage%5D=
    ja
&sentence_package%5Btranslation%5D%5Btext%5D=
&sentence_package%5Btranslation%5D%5Blanguage%5D=
    en
&sentence_package%5Bsound%5D%5Burl%5D=&sentence_package%5Bimage_url%5D=&commit=Add
    '''
    encoded_sample = urllib.parse.quote_plus(word['sample'], encoding='utf-8')
    encoded_trans = urllib.parse.quote_plus(trans, encoding='utf-8')
    definition = urllib.parse.quote_plus(word['definition'], encoding='utf-8')
    add_sentence_url = 'https://iknow.jp/custom/courses/{course_id}/items/{word_id}/sentences'.format(course_id=course_id, word_id=word_id)

    sample_text = 'utf8=%E2%9C%93&sentence_package%5Bsentence%5D%5Btext%5D=' + encoded_sample
    sample_translit = '&sentence_package%5Bsentence%5D%5Btransliteration%5D=' + encoded_trans
    translation = '&sentence_package%5Bsentence%5D%5Blanguage%5D=ja&sentence_package%5Btranslation%5D%5Btext%5D=' + definition
    end = '&sentence_package%5Btranslation%5D%5Blanguage%5D=en&sentence_package%5Bsound%5D%5Burl%5D=&sentence_package%5Bimage_url%5D=&commit=Add'

    sample_send_payload = sample_text + sample_translit + translation + end

    try:
        res = requests.post(add_sentence_url, data=sample_send_payload, headers=headers)
    except:
        no_sample_dict = {
            'course': course,
            'course_id': course_id,
            'word': word['word'],
            'word_id': word_id,
            'sentence': word['sample'],
        }
        failed_to_add_sample.append(no_sample_dict)
        print('Couldn\'t add sample sentence for word: ' + word['word'] + ' - request failed.') 
        print('Sample sentence is:')
        print(word['sample'])
    res.encoding = 'utf-8'
    if res.status_code != requests.codes.ok:
        # Mark as a word we couldn't add - will process later
        no_sample_dict = {
            'course': course,
            'course_id': course_id,
            'word': word['word'],
            'word_id': word_id,
            'sentence': word['sample'],
        }
        failed_to_add_sample.append(no_sample_dict)
        print('Couldn\'t add sample sentence for word: ' + word['word'] + ' - bad request return code.') 
        print('Sample sentence is:')
        print(word['sample'])

# Create a transliteration for the sample sentence
# Return empty string if we fail to transliterate
def create_transliteration(word: dict) -> str:
    try:
        transliterated_sample = kks.convert(word['sample'])
    except:
        print('Failed to transliterate sample sentence for word:' + word['word'])
        print(word['sample'])
        return ''
    trans = ''
    for item in transliterated_sample:
        trans += item['hira']
    return trans

# Add a new item to a iKnow course
# Returns empty string if we fail to create an item, or parse the response.
def create_new_item(course: str, course_id: str, word: dict, headers: dict) -> str:
    add_new_item_url = 'https://iknow.jp/custom/courses/{course_id}/items'.format(course_id=course_id)
    if word['word'] in previously_added or word['word'] in added:
        return ''
    if word['definition'] == BAD_DEF or word['reading'] == BAD_READING:
        # The kindle json couldn't figure these out, let's not add them and move on.
        print('Either bad reading or def for: ' + word['word'])
        fail_to_add_dict = {
            'course': course,
            'course_id': course_id,
            'word': word['word']
        }
        failed_to_add.append(fail_to_add_dict)
        return ''
    cur_word = urllib.parse.quote_plus(word['word'], encoding='utf-8')
    # Don't try to add words we've added in the past
    reading = urllib.parse.quote_plus(word['reading'], encoding='utf-8')
    definition = urllib.parse.quote_plus(word['definition'], encoding='utf-8')
    pos_list = word['part_of_speech'].split(',')
    pos = 'NONE' # Default to none
    # TODO: This chunk doesn't seem to work - part of speech wasn't added for any of my uploads
    for pos in pos_list:
        if pos.lower() in valid_parts_of_speech:
            pos = pos_map.get(pos)
            # Quit on first match
            break
    # implied else is either no PoS given, or can't map to anything. Keep as NONE
    '''
    This is the form iKnow sends:

    item[cue][text]=減点
    item[cue][language]=ja
    item[cue][transliteration]=げんてん
    item[cue][part_of_speech]=N
    item[response][text]=subtracting points
    item[response][language]=en
    '''
    cueString = 'item%5Bcue%5D%5Btext%5D={encodedCue}&item%5Bcue%5D%5Blanguage%5D={cueLang}&item%5Bcue%5D%5Btransliteration%5D={encodedCueTransliteration}&item%5Bcue%5D%5Bpart_of_speech%5D={cuePoS}'.format(encodedCue=cur_word, cueLang='ja', encodedCueTransliteration=reading, cuePoS=pos)
    responseString = '&item%5Bresponse%5D%5Btext%5D={responseText}&item%5Bresponse%5D%5Blanguage%5D={responseLang}'.format(responseText=definition, responseLang='en')
    payload = cueString + responseString
    '''
    Example payload:
item%5Bcue%5D%5Btext%5D=鼻歌&item%5Bcue%5D%5Blanguage%5D=jp&item%5Bcue%5D%5Btransliteration%5D=はなうた&item%5Bcue%5D%5Bpart_of_speech%5D=&item%5Bresponse%5D%5Btext%5D=humming, crooning&item%5Bresponse%5D%5Blanguage%5D=en
    '''
    try:
        res = requests.post(add_new_item_url, data=payload, headers=headers)
    except:
        fail_to_add_dict = {
            'course': course,
            'course_id': course_id,
            'word': word['word']
        }
        failed_to_add.append(fail_to_add_dict)
        print('Failed to post new word ' + word['word'])
        return ''
    # Handler for wierd bug I encountered where res came back as None- maybe just due to forced exit
    if not res:
        fail_to_add_dict = {
            'course': course,
            'course_id': course_id,
            'word': word['word']
        }
        failed_to_add.append(fail_to_add_dict)
        print('Failed to post new word ' + word['word'] + ' - no response')
        return ''
    res.encoding = 'utf-8'
    if res.status_code != requests.codes.ok:
        # Mark as a word we couldn't add
        fail_to_add_dict = {
            'course': course,
            'course_id': course_id,
            'word': word['word']
        }
        failed_to_add.append(fail_to_add_dict)
    else:
        added.add(word['word'])
    try:
        res_decoded = brotlicffi.decompress(res.content)
    except brotlicffi.Error as e:
        print(str(e))
        print('Could not decompress for word: ' + word['word'] + '\'s response')
        print(str(res.content))
        # Don't treat this as a failure to add. Just ensure that we don't try to add a sample sentence
        # and return a blank string
        return ''
    json_res = json.loads(res_decoded)
    # Grab the ID for the new flashcard we just added
    word_id = json_res['id']
    return word_id

# Creates a new iKnow course
# Returns an empty string if the request fails
def create_new_course(title: str, count: int,  headers: dict) -> str:
    course_title = title + ' ' + str(count)
    url = 'https://iknow.jp/custom/courses'
    course = urllib.parse.quote_plus(course_title)
    payload = 'utf8=%E2%9C%93&goal%5Bname%5D={name}&language={lang}&translation_language={l}&goal%5Bicon_image_url%5D=&commit=Create'.format(name=course, lang='ja', l='en')
    try:
        res = requests.post(url, data=payload, headers=headers)
    except:
        print('Failed to post new course ' + course_title)
        return ''
    res.encoding = 'utf-8'
    if res.status_code != requests.codes.ok:
        # Mark as a word we couldn't add - will process later
        print('Unable to make a new course!!')
        print('Provided title: ' + course_title)
        return ''
    try:
        res_decoded = brotlicffi.decompress(res.content)
    except brotlicffi.Error:
        print('Could not decompress our response from creating a course!')
        return ''
    # The response content is some jquery, which contains the course id
    response_content = res_decoded.decode('utf-8')
    match = re.search(r'/custom/courses/(\d*)', response_content)
    if not match:
        return ''
    else:
        course_id = str(match[1])
        return course_id


if __name__ == "__main__":
    print('Running')
    '''
    arg: cookie string
        firefox: Create an item/course manually, copy request headers, take everything in cookies
                should be one long string deliminated by ';', without newlines
    arg: csrf token
        firefox: same deal, but take the csrf token
    '''
    cookies = ''
    csrf_token = ''
    kindle_data = ''
    db_file = ''
    with open('generation_info.json', 'r') as g:
        info = json.load(g)
        cookies = info['cookies']
        csrf_token = info['csrf_token']
        kindle_data = info['kindle_data']
        db_file = info['vocab_db']
    
    if not kindle_data and not db_file:
        print('Supply a db path or kindle data path please.')
        print('Note if you supply both we will not use the kindle_data and instead generate from the DB')
        exit(1)
    if not cookies or not csrf_token:
        print('Need cookies and csrf token to upload data.')
        exit(1)
    if db_file:
        print('Creating kindle data from vocab.db file...')
        create_json_from_db(db_file)
        kindle_data = 'kindle_data.json'

    print('Starting import process...')
    convert_json_to_items(cookies, csrf_token, kindle_data)
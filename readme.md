# Import Kindle Lookup Information to iKnow

This is a simple script to import data from Kindle lookups into iKnow's (https://iknow.jp/) flashcard program.


When you read a Kindle book at highlight/lookup a word, it stores all the words looked up in an internal database called "vocab.db".  We can use the submodule "jp_kindle_lookup_to_json" to create a JSON file containing all the words with translations and sample sentences based on your book, and then import them into iKnow.  All this script needs from you is the vocab.db file (or output of running the kindle_to_json script), a list of cookies, and csrf token.



To get the list of cookies/csrf token, you will need to login to your iKnow account and try to either create a new course, or create a new item in a course with the Network tab of the Developer Console open in your browser.  From there, click on your request and copy the information.


To actually run the script, fill in the "generation_info.json" values with the cookie string (all one line, delimited by ';'), the csrf token string, and EITHER the vocab.db file to generate kindle data from, or the kindle data JSON file if you've already generated it.

If you provide both, the vocab.db file will take priority and we will generate the JSON from scratch.


Depending on how many words you're importing, this script may take a while to run, but once it does, you will get a "prior_results.json" file.  Please take care not to delete this file - it contains all words successfully imported, all words not imported, and all words without a sample sentence added.  The failures to import can happen for a variety of reasons, so if the data looks good, just try again later and hopefully it'll resolve itself.  

The JSON file created also stores how many courses we've created for a book, and how many items are in the last course.  Why are we creating multiple courses per book?  iKnow themselves recommend a max of 100 words/course, so that's how I have the script setup - we use book titles + a counter to determine what we name the courses, so you'll have "Harry Potter 0" and "Harry Potter 1" if you're importing 130 words, for example.




Note that all the requests made are reverse-engineered; iKnow doesn't have a public API, so if endpoints change, this script can break without warning.
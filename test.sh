#!/bin/sh -e 
# This is a simple test script that tests a few rsync
# features to make sure I haven't broken them before a release. Thanks
# to Phil Hands for writing this

export PATH=.:$PATH
TMP=/tmp/rsync-test.$$
F1=README

mkdir $TMP

pause() {
    echo ... press enter to continue
    read
}

echo "Test 1 basic operation"
rsync -av testin/ ${TMP}/rsync
diff -ur testin/ ${TMP}/rsync
pause

echo "Test 2 - one file"
rm ${TMP}/rsync/${F1}
rsync -av testin/ ${TMP}/rsync
diff -ur testin/ ${TMP}/rsync
pause

echo "Test 3 - extra data"
echo "extra line" >> ${TMP}/rsync/${F1}
rsync -av testin/ ${TMP}/rsync
diff -ur testin/ ${TMP}/rsync
pause

echo "Test 4 - --delete"
cp testin/${F1} ${TMP}/rsync/f1
rsync --delete -av testin/ ${TMP}/rsync
diff -ur testin/ ${TMP}/rsync
pause

echo "Test 5 (uses ssh, so will fail if you don't have it) "
rm -rf ${TMP}/rsync
rsync -av -e ssh testin/ localhost:${TMP}/rsync
diff -ur testin/ ${TMP}/rsync
pause

echo "Test 6 (uses ssh, so will fail if you don't have it) "
mv ${TMP}/rsync/${F1} ${TMP}/rsync/f1
rsync --delete -av -e ssh testin/ localhost:${TMP}/rsync
diff -ur testin/ ${TMP}/rsync
pause

rm -rf ${TMP}

echo Tests Completed

#!/bin/sh

#
# Copyright (C) 1998 Philip Hands <http://www.hands.com/~phil/>
#
# This program is distributable under the terms of the GNU GPL (see COPYING)
#
# This is a simple test script that tests a few rsync
# features to make sure I haven't broken them before a release.
#
#

cat <<EOF

This set of tests is not completely portable. It is intended for developers
not for end users. You may experience failures on some platforms that
do not indicate a problem with rsync.

EOF

export PATH=.:$PATH
TMP=/tmp/rsync-test.$$
FROM=${TMP}/from
TO=${TMP}/to
F1=text1
LOG=${TMP}/log

mkdir $TMP
mkdir $FROM
mkdir $TO

# set up test data
touch ${FROM}/empty
mkdir ${FROM}/emptydir
ps ax > ${FROM}/pslist
echo -n "This file has no trailing lf" > ${FROM}/nolf
ln -s nolf ${FROM}/nolf-symlink
cat /etc/inittab /etc/services /etc/resolv.conf > ${FROM}/${F1}
mkdir ${FROM}/dir
cp ${FROM}/${F1} ${FROM}/dir

checkit() {
  echo -n "Test $4: $5:"
  log=${LOG}.$4
  failed=
  echo "Running: \"$1\""  >${log}
  echo "">>${log}
  eval "$1 || failed=YES"  >>${log} 2>&1

  echo "-------------">>${log}
  echo "check how the files compare with diff:">>${log}
  echo "">>${log}
  diff -ur $2 $3 >>${log} || failed=YES
  echo "-------------">>${log}
  echo "check how the directory listings compare with diff:">>${log}
  echo "">>${log}
  ls -la $2 > ${TMP}/ls-from
  ls -la $3 > ${TMP}/ls-to
  diff -u ${TMP}/ls-from ${TMP}/ls-to >>${log} || failed=YES
  if [ -z "${failed}" ] ; then
    echo "	done."
    rm $log
  else
    echo "	FAILED."
  fi
}

checkforlogs() {
  if [ -f $1 ] ; then
    cat <<EOF

Failures have occured.

You can find the output of the tests in these files:
  $@

Please hit <RETURN>
EOF
  read input
  else

    rm -rf ${TMP}
    echo ""
    echo "Tests Completed Successfully :-)"
  fi
}

# Main script starts here

checkit "rsync -av ${FROM}/ ${TO}" ${FROM}/ ${TO} \
  1 "basic operation"

ln ${FROM}/pslist ${FROM}/dir
checkit "rsync -avH ${FROM}/ ${TO}" ${FROM}/ ${TO} \
  2 "hard links"

rm ${TO}/${F1}
checkit "rsync -avH ${FROM}/ ${TO}" ${FROM}/ ${TO} \
  3 "one file"

echo "extra line" >> ${TO}/${F1}
checkit "rsync -avH ${FROM}/ ${TO}" ${FROM}/ ${TO} \
  4 "extra data"

cp ${FROM}/${F1} ${TO}/ThisShouldGo
checkit "rsync --delete -avH ${FROM}/ ${TO}" ${FROM}/ ${TO} \
  5 " --delete"

LONGDIR=${FROM}/This-is-a-directory-with-a-stupidly-long-name-created-in-an-attempt-to-provoke-an-error-found-in-2.0.11-that-should-hopefully-never-appear-again-if-this-test-does-its-job/This-is-a-directory-with-a-stupidly-long-name-created-in-an-attempt-to-provoke-an-error-found-in-2.0.11-that-should-hopefully-never-appear-again-if-this-test-does-its-job/This-is-a-directory-with-a-stupidly-long-name-created-in-an-attempt-to-provoke-an-error-found-in-2.0.11-that-should-hopefully-never-appear-again-if-this-test-does-its-job
mkdir -p ${LONGDIR}
date > ${LONGDIR}/1
ls -la / > ${LONGDIR}/2
checkit "rsync --delete -avH ${FROM}/ ${TO}" ${FROM}/ ${TO} \
  6 "long paths"

if type ssh >/dev/null ; then
rm -rf ${TO}
  checkit "rsync -avH -e ssh ${FROM}/ localhost:${TO}" ${FROM}/ ${TO} \
    7 "ssh: basic test"

  mv ${TO}/${F1} ${TO}/ThisShouldGo
  checkit "rsync --delete -avH -e ssh ${FROM}/ localhost:${TO}" ${FROM}/ ${TO}\
    8 "ssh: renamed file"
else
  echo ""
  echo "**** Skipping SSH tests because ssh is not in the path ****"
  echo ""
fi

checkforlogs ${LOG}.?

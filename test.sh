#!/bin/sh

# Copyright (C) 1998,1999 Philip Hands <phil@hands.com>
#
# This program is distributable under the terms of the GNU GPL (see COPYING)
#
# This is a simple test script that tests a few rsync
# features to make sure I haven't broken them before a release.
#
#

# check if we are running under debian-test, and change behaviour to suit
if test -n "${DEBIANTEST_LIB}" ; then
  # make sure rsync is installed
  test -e /usr/bin/rsync || exit 0
  
  . ${DEBIANTEST_LIB}/functions.sh
  Debian=1
else
  cat <<EOF

This set of tests is not completely portable. It is intended for developers
not for end users. You may experience failures on some platforms that
do not indicate a problem with rsync.

EOF

RSYNC=`pwd`/rsync

  runtest() {
    echo -n "Test $1: "
    eval "$2"
  }
  printmsg() {
    echo ""
    echo "**** ${1}^G ****"
    echo ""  
  }
fi

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

# Gather some random text.  We need files that will exist and be
# publicly readable on all platforms: hopefully this will work.
cat /etc/*tab /etc/services /etc/*.conf /etc/*rc > ${FROM}/${F1}

mkdir ${FROM}/dir
cp ${FROM}/${F1} ${FROM}/dir/
mkdir ${FROM}/dir/subdir
mkdir ${FROM}/dir/subdir/subsubdir
ls -ltr /etc > ${FROM}/dir/subdir/subsubdir/etc-ltr-list
mkdir ${FROM}/dir/subdir/subsubdir2
ls -lt /bin > ${FROM}/dir/subdir/subsubdir2/bin-lt-list

checkit() {
  testnum=`expr 0${testnum} + 1`
  log=${LOG}.${testnum}
  failed=
  echo "Running: \"$1\""  >${log}
  echo "">>${log}
  eval "$1"  >>${log} 2>&1
  status=$?
  if [ $status != 0 ]; then
    failed="YES";
  fi
  echo "-------------">>${log}
  echo "check how the files compare with diff:">>${log}
  echo "">>${log}
  diff -ur $2 $3 >>${log} 2>&1 || failed=YES
  echo "-------------">>${log}
  echo "check how the directory listings compare with diff:">>${log}
  echo "">>${log}
  ( cd $2 ; ls -laR ) > ${TMP}/ls-from 2>>${log}
  ( cd $3 ; ls -laR ) > ${TMP}/ls-to  2>>${log}
  diff -u ${TMP}/ls-from ${TMP}/ls-to >>${log} 2>&1 || failed=YES
  if [ -z "${failed}" ] ; then
    test -z "${Debian}" && echo "	done."
    rm $log
    return 0
  else
    if test -n "${Debian}" ; then
      cat ${log}
      rm ${log}
    else
      echo "	FAILED (test # ${testnum} status=$status)."
    fi
    return 1
  fi
}


checkforlogs() {
  # skip it if we're under debian-test
  if test -n "${Debian}" ; then return 0 ; fi

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

runtest "basic operation" 'checkit "$RSYNC -av ${FROM}/ ${TO}" ${FROM}/ ${TO}'

ln ${FROM}/pslist ${FROM}/dir
runtest "hard links" 'checkit "$RSYNC -avH ${FROM}/ ${TO}" ${FROM}/ ${TO}'

rm ${TO}/${F1}
runtest "one file" 'checkit "$RSYNC -avH ${FROM}/ ${TO}" ${FROM}/ ${TO}'

echo "extra line" >> ${TO}/${F1}
runtest "extra data" 'checkit "$RSYNC -avH ${FROM}/ ${TO}" ${FROM}/ ${TO}'

cp ${FROM}/${F1} ${TO}/ThisShouldGo
runtest " --delete" 'checkit "$RSYNC --delete -avH ${FROM}/ ${TO}" ${FROM}/ ${TO}'

LONGDIR=${FROM}/This-is-a-directory-with-a-stupidly-long-name-created-in-an-attempt-to-provoke-an-error-found-in-2.0.11-that-should-hopefully-never-appear-again-if-this-test-does-its-job/This-is-a-directory-with-a-stupidly-long-name-created-in-an-attempt-to-provoke-an-error-found-in-2.0.11-that-should-hopefully-never-appear-again-if-this-test-does-its-job/This-is-a-directory-with-a-stupidly-long-name-created-in-an-attempt-to-provoke-an-error-found-in-2.0.11-that-should-hopefully-never-appear-again-if-this-test-does-its-job
mkdir -p ${LONGDIR}
date > ${LONGDIR}/1
ls -la / > ${LONGDIR}/2
runtest "long paths" 'checkit "$RSYNC --delete -avH ${FROM}/ ${TO}" ${FROM}/ ${TO}'

if type ssh >/dev/null 2>&1; then
  if [ "`ssh -o'BatchMode yes' localhost echo yes 2>/dev/null`" = "yes" ]; then
  rm -rf ${TO}
    runtest "ssh: basic test" 'checkit "$RSYNC -avH -e ssh --rsync-path=$RSYNC ${FROM}/ localhost:${TO}" ${FROM}/ ${TO}'

    mv ${TO}/${F1} ${TO}/ThisShouldGo
    runtest "ssh: renamed file" 'checkit "$RSYNC --delete -avH -e ssh --rsync-path=$RSYNC ${FROM}/ localhost:${TO}" ${FROM}/ ${TO}'
  else
  printmsg "Skipping SSH tests because ssh conection to localhost not authorised"
  fi
else
  printmsg "Skipping SSH tests because ssh is not in the path"
fi

rm -rf ${TO}
mkdir -p ${FROM}2/dir/subdir
cp -a ${FROM}/dir/subdir/subsubdir ${FROM}2/dir/subdir
cp ${FROM}/dir/* ${FROM}2/dir 2>/dev/null
runtest "excludes" 'checkit "$RSYNC -vv -Hlrt --delete --include /dir/ --include /dir/\* --include /dir/\*/subsubdir  --include /dir/\*/subsubdir/\*\* --exclude \*\* ${FROM}/dir ${TO}" ${FROM}2/ ${TO}'
rm -r ${FROM}2

checkforlogs ${LOG}.?

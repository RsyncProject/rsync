#!/bin/sh

run() {
    prog=$1; shift
    name=$1; shift
    answer=$1; shift

    echo Running test $name.

    result=`./$prog $*`
    if [ "$answer" != "$result" ]; then
	echo "Test \"$*\" failed with: $result"
	exit 2
    fi
}

make -q testcases

run test1 "test1 - 1" "arg1: 1 arg2: (none)" --arg1
run test1 "test1 - 2" "arg1: 0 arg2: foo" --arg2 foo
run test1 "test1 - 3" "arg1: 1 arg2: something" --arg1 --arg2 something
run test1 "test1 - 4" "arg1: 0 arg2: another" --simple another
run test1 "test1 - 5" "arg1: 1 arg2: alias" --two
run test1 "test1 - 6" "arg1: 1 arg2: (none) rest: --arg2" --arg1 -- --arg2 
run test1 "test1 - 7" "arg1: 0 arg2: abcd rest: --arg1" --simple abcd -- --arg1 
run test1 "test1 - 8" "arg1: 1 arg2: (none) rest: --arg2" --arg1 --takerest --arg2 
run test1 "test1 - 9" "arg1: 0 arg2: foo" -2 foo
run test1 "test1 - 10" "arg1: 0 arg2: (none) arg3: 50" -3 50
run test1 "test1 - 11" "arg1: 0 arg2: bar" -T bar
run test1 "test1 - 12" "arg1: 1 arg2: (none)" -O 
run test1 "test1 - 13" "arg1: 1 arg2: foo" -OT foo
run test1 "test1 - 14" "arg1: 0 arg2: (none) inc: 1" --inc
run test1 "test1 - 15" "arg1: 0 arg2: foo inc: 1" -i --arg2 foo
POSIX_ME_HARDER=1 run test1 "test1 - 16" "arg1: 1 arg2: (none) rest: foo --arg2 something" --arg1 foo --arg2 something
POSIXLY_CORRECT=1 run test1 "test1 - 17" "arg1: 1 arg2: (none) rest: foo --arg2 something" --arg1 foo --arg2 something
run test1 "test1 - 18" "callback: c sampledata bar arg1: 1 arg2: (none)" --arg1 --cb bar
run test1 "test1 - 19" "./test1 ;" --echo-args
run test1 "test1 - 20" "./test1 ; --arg1" --echo-args --arg1
run test1 "test1 - 21" "./test1 ; --arg2 something" -T something -e
run test1 "test1 - 22" "./test1 ; --arg2 something -- more args" -T something -a more args
run test1 "test1 - 23" "./test1 ; --echo-args -a" --echo-args -e -a
run test1 "test1 - 24" "arg1: 0 arg2: (none) short: 1" -shortoption
run test1 "test1 - 25" "arg1: 0 arg2: (none) short: 1" --shortoption
run test1 "test1 - 26" "callback: c arg for cb2 foo arg1: 0 arg2: (none)" --cb2 foo

echo ""
echo "Passed."

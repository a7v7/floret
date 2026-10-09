// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Anthony Verbeck
//------------------------------------------------------------------------------
//  check.h - a minimal unit-test harness: no dependencies, plain C11.
//
//      CHECK(cond)          record a failure if cond is false, keep going
//      CHECK_EQ(a, b)       same, printing both values (as unsigned long)
//      RUN(test_fn)         run one test function
//      check_report()       print the summary; returns the process exit code
//------------------------------------------------------------------------------
#ifndef CHECK_H
#define CHECK_H

#include <stdio.h>

static int check_failures;
static int check_tests;

#define CHECK(cond)                                                            \
    do                                                                         \
    {                                                                          \
        if(!(cond))                                                            \
        {                                                                      \
            printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);           \
            check_failures++;                                                  \
        }                                                                      \
    } while(0)

#define CHECK_EQ(a, b)                                                         \
    do                                                                         \
    {                                                                          \
        unsigned long check_a_ = (unsigned long)(a);                           \
        unsigned long check_b_ = (unsigned long)(b);                           \
        if(check_a_ != check_b_)                                               \
        {                                                                      \
            printf("  FAIL %s:%d: %s == %s (%lu != %lu)\n", __FILE__,          \
                   __LINE__, #a, #b, check_a_, check_b_);                      \
            check_failures++;                                                  \
        }                                                                      \
    } while(0)

#define RUN(fn)                                                                \
    do                                                                         \
    {                                                                          \
        int before_ = check_failures;                                          \
        check_tests++;                                                         \
        fn();                                                                  \
        printf("%s %s\n", check_failures == before_ ? "ok  " : "FAIL", #fn);   \
    } while(0)

static inline int check_report(void)
{
    printf("\n%d tests, %d failed checks\n", check_tests, check_failures);
    return check_failures == 0 ? 0 : 1;
}

#endif // CHECK_H

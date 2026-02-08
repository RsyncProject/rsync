/*
 * Fuzz rsync's wildmatch pattern matching.
 *
 * Build with: make -C fuzz
 * Or via OSS-Fuzz integration.
 */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>

/* Declarations from lib/wildmatch.h */
int wildmatch(const char *pattern, const char *text);
int iwildmatch(const char *pattern, const char *text);
int wildmatch_array(const char *pattern, const char *const *texts, int where);

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 2 || size > 4096)
        return 0;

    /* Find a null byte to split input into pattern and text */
    const uint8_t *sep = memchr(data, '\0', size);
    char *pattern, *text;

    if (sep && sep > data && (sep - data) < (ptrdiff_t)(size - 1)) {
        size_t pat_len = sep - data;
        size_t txt_len = size - pat_len - 1;
        pattern = (char *)malloc(pat_len + 1);
        text = (char *)malloc(txt_len + 1);
        if (!pattern || !text) { free(pattern); free(text); return 0; }
        memcpy(pattern, data, pat_len);
        pattern[pat_len] = '\0';
        memcpy(text, sep + 1, txt_len);
        text[txt_len] = '\0';
    } else {
        size_t mid = size / 2;
        pattern = (char *)malloc(mid + 1);
        text = (char *)malloc((size - mid) + 1);
        if (!pattern || !text) { free(pattern); free(text); return 0; }
        memcpy(pattern, data, mid);
        pattern[mid] = '\0';
        memcpy(text, data + mid, size - mid);
        text[size - mid] = '\0';
    }

    wildmatch(pattern, text);
    iwildmatch(pattern, text);

    const char *texts[2] = { text, NULL };
    wildmatch_array(pattern, texts, 0);

    free(pattern);
    free(text);
    return 0;
}

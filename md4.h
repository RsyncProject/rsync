/*
   This code is from rfc1186. 
*/

 /*
 ** ********************************************************************
 ** md4.h -- Header file for implementation of                        **
 ** MD4 Message Digest Algorithm                                      **
 ** Updated: 2/13/90 by Ronald L. Rivest                              **
 ** (C) 1990 RSA Data Security, Inc.                                  **
 ** ********************************************************************
 */

 /* MDstruct is the data structure for a message digest computation.
 */
 typedef struct {
   unsigned int32 buffer[4]; /* Holds 4-word result of MD computation */
   unsigned char count[8]; /* Number of bits processed so far */
   unsigned int done;      /* Nonzero means MD computation finished */
 } MDstruct, *MDptr;

 /* MDbegin(MD)



 ** Input: MD -- an MDptr
 ** Initialize the MDstruct prepatory to doing a message digest
 ** computation.
 */
 extern void MDbegin();

 /* MDupdate(MD,X,count)
 ** Input: MD -- an MDptr
 **        X -- a pointer to an array of unsigned characters.
 **        count -- the number of bits of X to use (an unsigned int).
 ** Updates MD using the first "count" bits of X.
 ** The array pointed to by X is not modified.
 ** If count is not a multiple of 8, MDupdate uses high bits of
 ** last byte.
 ** This is the basic input routine for a user.
 ** The routine terminates the MD computation when count < 512, so
 ** every MD computation should end with one call to MDupdate with a
 ** count less than 512.  Zero is OK for a count.
 */
 extern void MDupdate();

 /*
 ** End of md4.h
 */

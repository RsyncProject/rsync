/* -*- c-file-style: "linux" -*-
   
   Copyright (C) 1996-2000 by Andrew Tridgell
   Copyright (C) Paul Mackerras 1996
   
   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2 of the License, or
   (at your option) any later version.
   
   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.
   
   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
*/

#include "rsync.h"

extern int verbose;
extern int recurse;
extern int delete_mode;
extern int remote_version;
extern int csum_length;
extern struct stats stats;
extern int dry_run;
extern int am_server;
extern int relative_paths;
extern int preserve_hard_links;
extern int cvs_exclude;
extern int io_error;
extern char *tmpdir;
extern char *compare_dest;
extern int make_backups;
extern char *backup_suffix;

static struct delete_list {
	DEV64_T dev;
	INO64_T inode;
} *delete_list;
static int dlist_len, dlist_alloc_len;

/* yuck! This function wouldn't have been necessary if I had the sorting
   algorithm right. Unfortunately fixing the sorting algorithm would introduce
   a backward incompatibility as file list indexes are sent over the link.
*/
static int delete_already_done(struct file_list *flist,int j)
{
	int i;
	STRUCT_STAT st;

	if (link_stat(f_name(flist->files[j]), &st)) return 1;

	for (i=0;i<dlist_len;i++) {
		if (st.st_ino == delete_list[i].inode &&
		    st.st_dev == delete_list[i].dev)
			return 1;
	}

	return 0;
}

static void add_delete_entry(struct file_struct *file)
{
	if (dlist_len == dlist_alloc_len) {
		dlist_alloc_len += 1024;
		delete_list = realloc_array(delete_list, struct delete_list,
					    dlist_alloc_len);
		if (!delete_list) out_of_memory("add_delete_entry");
	}

	delete_list[dlist_len].dev = file->dev;
	delete_list[dlist_len].inode = file->inode;
	dlist_len++;

	if (verbose > 3)
		rprintf(FINFO,"added %s to delete list\n", f_name(file));
}

static void delete_one(struct file_struct *f)
{
	if (!S_ISDIR(f->mode)) {
		if (robust_unlink(f_name(f)) != 0) {
			rprintf(FERROR,"delete_one: unlink %s: %s\n",f_name(f),strerror(errno));
		} else if (verbose) {
			rprintf(FINFO,"deleting %s\n",f_name(f));
		}
	} else {    
		if (do_rmdir(f_name(f)) != 0) {
			if (errno != ENOTEMPTY && errno != EEXIST)
				rprintf(FERROR,"delete_one: rmdir %s: %s\n",
                                        f_name(f), strerror(errno));
		} else if (verbose) {
			rprintf(FINFO,"deleting directory %s\n",f_name(f));      
		}
	}
}




/* this deletes any files on the receiving side that are not present
   on the sending side. For version 1.6.4 I have changed the behaviour
   to match more closely what most people seem to expect of this option */
void delete_files(struct file_list *flist)
{
	struct file_list *local_file_list;
	int i, j;
	char *name;
	extern int module_id;
	extern int ignore_errors;
	extern int max_delete;
	static int deletion_count;

	if (cvs_exclude)
		add_cvs_excludes();

	if (io_error && !(lp_ignore_errors(module_id) || ignore_errors)) {
		rprintf(FINFO,"IO error encountered - skipping file deletion\n");
		return;
	}

	for (j=0;j<flist->count;j++) {
		if (!S_ISDIR(flist->files[j]->mode) || 
		    !(flist->files[j]->flags & FLAG_DELETE)) continue;

		if (remote_version < 19 &&
		    delete_already_done(flist, j)) continue;

		name = strdup(f_name(flist->files[j]));

		if (!(local_file_list = send_file_list(-1,1,&name))) {
			free(name);
			continue;
		}

		if (verbose > 1)
			rprintf(FINFO,"deleting in %s\n", name);

		for (i=local_file_list->count-1;i>=0;i--) {
			if (max_delete && deletion_count > max_delete) break;
			if (!local_file_list->files[i]->basename) continue;
			if (remote_version < 19 &&
			    S_ISDIR(local_file_list->files[i]->mode))
				add_delete_entry(local_file_list->files[i]);
			if (-1 == flist_find(flist,local_file_list->files[i])) {
				char *f = f_name(local_file_list->files[i]);
				int k = strlen(f) - strlen(backup_suffix);
/* Hi Andrew, do we really need to play with backup_suffix here? */
				if (make_backups && ((k <= 0) ||
					    (strcmp(f+k,backup_suffix) != 0))) {
					(void) make_backup(f);
				} else {
					deletion_count++;
					delete_one(local_file_list->files[i]);
				}
			}
		}
		flist_free(local_file_list);
		free(name);
	}
}


static int get_tmpname(char *fnametmp, char *fname)
{
	char *f;

	/* open tmp file */
	if (tmpdir) {
		f = strrchr(fname,'/');
		if (f == NULL) 
			f = fname;
		else 
			f++;
		if (strlen(tmpdir)+strlen(f)+10 > MAXPATHLEN) {
			rprintf(FERROR,"filename too long\n");
			return 0;
		}
		snprintf(fnametmp,MAXPATHLEN, "%s/.%s.XXXXXX",tmpdir,f);
		return 1;
	} 

	f = strrchr(fname,'/');

	if (strlen(fname)+9 > MAXPATHLEN) {
		rprintf(FERROR,"filename too long\n");
		return 0;
	}

	if (f) {
		*f = 0;
		snprintf(fnametmp,MAXPATHLEN,"%s/.%s.XXXXXX",
			 fname,f+1);
		*f = '/';
	} else {
		snprintf(fnametmp,MAXPATHLEN,".%s.XXXXXX",fname);
	}

	return 1;
}


static int receive_data(int f_in,struct map_struct *buf,int fd,char *fname,
			OFF_T total_size)
{
	int i;
	unsigned int n,remainder,len,count;
	OFF_T offset = 0;
	OFF_T offset2;
	char *data;
	static char file_sum1[MD4_SUM_LENGTH];
	static char file_sum2[MD4_SUM_LENGTH];
	char *map=NULL;
	
	count = read_int(f_in);
	n = read_int(f_in);
	remainder = read_int(f_in);
	
	sum_init();
	
	for (i=recv_token(f_in,&data); i != 0; i=recv_token(f_in,&data)) {

		show_progress(offset, total_size);

		if (i > 0) {
			extern int cleanup_got_literal;

			if (verbose > 3) {
				rprintf(FINFO,"data recv %d at %.0f\n",
					i,(double)offset);
			}

			stats.literal_data += i;
			cleanup_got_literal = 1;
      
			sum_update(data,i);

			if (fd != -1 && write_file(fd,data,i) != i) {
				rprintf(FERROR,"write failed on %s : %s\n",fname,strerror(errno));
				exit_cleanup(RERR_FILEIO);
			}
			offset += i;
			continue;
		} 

		i = -(i+1);
		offset2 = i*(OFF_T)n;
		len = n;
		if (i == (int) count-1 && remainder != 0)
			len = remainder;
		
		stats.matched_data += len;
		
		if (verbose > 3)
			rprintf(FINFO,"chunk[%d] of size %d at %.0f offset=%.0f\n",
				i,len,(double)offset2,(double)offset);
		
		if (buf) {
			map = map_ptr(buf,offset2,len);
		
			see_token(map, len);
			sum_update(map,len);
		}
		
		if (fd != -1 && write_file(fd,map,len) != (int) len) {
			rprintf(FERROR,"write failed on %s : %s\n",
				fname,strerror(errno));
			exit_cleanup(RERR_FILEIO);
		}
		offset += len;
	}

	end_progress(total_size);

	if (fd != -1 && offset > 0 && sparse_end(fd) != 0) {
		rprintf(FERROR,"write failed on %s : %s\n",
			fname,strerror(errno));
		exit_cleanup(RERR_FILEIO);
	}

	sum_end(file_sum1);

	if (remote_version >= 14) {
		read_buf(f_in,file_sum2,MD4_SUM_LENGTH);
		if (verbose > 2) {
			rprintf(FINFO,"got file_sum\n");
		}
		if (fd != -1 && 
		    memcmp(file_sum1,file_sum2,MD4_SUM_LENGTH) != 0) {
			return 0;
		}
	}
	return 1;
}


/**
 * main routine for receiver process.
 *
 * Receiver process runs on the same host as the generator process. */
int recv_files(int f_in,struct file_list *flist,char *local_name,int f_gen)
{  
	int fd1,fd2;
	STRUCT_STAT st;
	char *fname;
	char template[MAXPATHLEN];
	char fnametmp[MAXPATHLEN];
	char *fnamecmp;
	char fnamecmpbuf[MAXPATHLEN];
	struct map_struct *buf;
	int i;
	struct file_struct *file;
	int phase=0;
	int recv_ok;
	extern struct stats stats;		
	extern int preserve_perms;
	extern int delete_after;
	extern int orig_umask;
	struct stats initial_stats;

	if (verbose > 2) {
		rprintf(FINFO,"recv_files(%d) starting\n",flist->count);
	}

	while (1) {      
		cleanup_disable();

		i = read_int(f_in);
		if (i == -1) {
			if (phase==0 && remote_version >= 13) {
				phase++;
				csum_length = SUM_LENGTH;
				if (verbose > 2)
					rprintf(FINFO,"recv_files phase=%d\n",phase);
				write_int(f_gen,-1);
				continue;
			}
			break;
		}

		if (i < 0 || i >= flist->count) {
			rprintf(FERROR,"Invalid file index %d in recv_files (count=%d)\n", 
				i, flist->count);
			exit_cleanup(RERR_PROTOCOL);
		}

		file = flist->files[i];
		fname = f_name(file);

		stats.num_transferred_files++;
		stats.total_transferred_size += file->length;

		if (local_name)
			fname = local_name;

		if (dry_run) {
			if (!am_server) {
				log_transfer(file, fname);
			}
			continue;
		}

		initial_stats = stats;

		if (verbose > 2)
			rprintf(FINFO,"recv_files(%s)\n",fname);

		fnamecmp = fname;

		/* open the file */  
		fd1 = do_open(fnamecmp, O_RDONLY, 0);

		if ((fd1 == -1) && (compare_dest != NULL)) {
			/* try the file at compare_dest instead */
			snprintf(fnamecmpbuf,MAXPATHLEN,"%s/%s",
						compare_dest,fname);
			fnamecmp = fnamecmpbuf;
			fd1 = do_open(fnamecmp, O_RDONLY, 0);
		}

		if (fd1 != -1 && do_fstat(fd1,&st) != 0) {
			rprintf(FERROR,"fstat %s : %s\n",fnamecmp,strerror(errno));
			receive_data(f_in,NULL,-1,NULL,file->length);
			close(fd1);
			continue;
		}

		if (fd1 != -1 && !S_ISREG(st.st_mode)) {
			rprintf(FERROR,"%s : not a regular file (recv_files)\n",fnamecmp);
			receive_data(f_in,NULL,-1,NULL,file->length);
			close(fd1);
			continue;
		}

		if (fd1 != -1 && !preserve_perms) {
			/* if the file exists already and we aren't perserving
			   presmissions then act as though the remote end sent
			   us the file permissions we already have */
			file->mode = st.st_mode;
		}

		if (fd1 != -1 && st.st_size > 0) {
			buf = map_file(fd1,st.st_size);
			if (verbose > 2)
				rprintf(FINFO,"recv mapped %s of size %.0f\n",fnamecmp,(double)st.st_size);
		} else {
			buf = NULL;
		}

		if (!get_tmpname(fnametmp,fname)) {
			if (buf) unmap_file(buf);
			if (fd1 != -1) close(fd1);
			continue;
		}

		strlcpy(template, fnametmp, sizeof(template));

		/* we initially set the perms without the
		   setuid/setgid bits to ensure that there is no race
		   condition. They are then correctly updated after
		   the lchown. Thanks to snabb@epipe.fi for pointing
		   this out.  We also set it initially without group
		   access because of a similar race condition. */
		fd2 = do_mkstemp(fnametmp, file->mode & INITACCESSPERMS);

		/* in most cases parent directories will already exist
		   because their information should have been previously
		   transferred, but that may not be the case with -R */
		if (fd2 == -1 && relative_paths && errno == ENOENT && 
		    create_directory_path(fnametmp, orig_umask) == 0) {
			strlcpy(fnametmp, template, sizeof(fnametmp));
			fd2 = do_mkstemp(fnametmp, file->mode & INITACCESSPERMS);
		}
		if (fd2 == -1) {
			rprintf(FERROR,"mkstemp %s failed: %s\n",fnametmp,strerror(errno));
			receive_data(f_in,buf,-1,NULL,file->length);
			if (buf) unmap_file(buf);
			if (fd1 != -1) close(fd1);
			continue;
		}
      
		cleanup_set(fnametmp, fname, file, buf, fd1, fd2);

		if (!am_server) {
			log_transfer(file, fname);
		}

		/* recv file data */
		recv_ok = receive_data(f_in,buf,fd2,fname,file->length);

		log_recv(file, &initial_stats);
		
		if (buf) unmap_file(buf);
		if (fd1 != -1) {
			close(fd1);
		}
		close(fd2);
		
		if (verbose > 2)
			rprintf(FINFO,"renaming %s to %s\n",fnametmp,fname);

		finish_transfer(fname, fnametmp, file);

		cleanup_disable();

		if (!recv_ok) {
			if (csum_length == SUM_LENGTH) {
				rprintf(FERROR,"ERROR: file corruption in %s. File changed during transfer?\n",
					fname);
			} else {
				if (verbose > 1)
					rprintf(FINFO,"redoing %s(%d)\n",fname,i);
				write_int(f_gen,i);
			}
		}
	}

	if (delete_after) {
		if (recurse && delete_mode && !local_name && flist->count>0) {
			delete_files(flist);
		}
	}

	if (preserve_hard_links)
		do_hard_links();

	/* now we need to fix any directory permissions that were 
	   modified during the transfer */
	for (i = 0; i < flist->count; i++) {
		file = flist->files[i];
		if (!file->basename || !S_ISDIR(file->mode)) continue;
		recv_generator(local_name?local_name:f_name(file),flist,i,-1);
	}

	if (verbose > 2)
		rprintf(FINFO,"recv_files finished\n");
	
	return 0;
}


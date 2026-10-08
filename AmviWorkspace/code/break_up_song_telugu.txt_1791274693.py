#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Structure to represent a song
typedef struct {
    char title[50];
    char artist[50];
    char duration[10];
} Song;

// Function to sort songs by duration
void sortSongs(Song* songs, int size) {
    // Use bubble sort for simplicity
    for (int i = 0; i < size - 1; i++) {
        for (int j = 0; j < size - i - 1; j++) {
            // Compare duration of two songs
            if (strcmp(songs[j].duration, songs[j + 1].duration) > 0) {
                // Swap songs if duration is in the wrong order
                Song temp = songs[j];
                songs[j] = songs[j + 1];
                songs[j + 1] = temp;
            }
        }
    }
}

// Function to print a song
void printSong(Song song) {
    printf("Title: %s\n", song.title);
    printf("Artist: %s\n", song.artist);
    printf("Duration: %s\n", song.duration);
}

int main() {
    // Define some songs
    Song songs[] = {
        {"ఒక బిడ్డా", "ఎస్.కె", "4:20"},
        {"దేశం", "కేసీ", "3:40"},
        {"మనసు", "విశ్వజిత్", "5:10"}
    };

    // Print unsorted songs
    printf("Unsorted Songs:\n");
    for (int i = 0; i < 3; i++) {
        printSong(songs[i]);
        printf("\n");
    }

    // Sort songs by duration
    int size = sizeof(songs) / sizeof(songs[0]);
    sortSongs(songs, size);

    // Print sorted songs
    printf("Sorted Songs:\n");
    for (int i = 0; i < 3; i++) {
        printSong(songs[i]);
        printf("\n");
    }

    return 0;
}
